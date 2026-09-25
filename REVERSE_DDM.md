# DDM format reverse engineering — analysis status

## 1. Context

Goal: reconstruct the 3D models stored in a PS3 game's `.ddm` files from the
extracted ISO.

Two reference files were used during the analysis:

| File            | Role / description                         |
| --------------- | ------------------------------------------ |
| `chr300_c01`    | Relatively small model resembling a sword  |
| `chr310_c01(1)` | Larger model resembling a large axe        |

These files are especially useful because they appear to share the same
structure while having different sizes, vertex counts, and material counts.

The final goal is to build a Python decoder that can:

1. read a DDM file;
2. identify its internal structures;
3. reconstruct every mesh and submesh;
4. recover positions, normals, tangents, UVs, materials, and related data;
5. export to OBJ, glTF, or FBX;
6. work on the other DDM files from the ISO.

---

# 2. Current understanding

The format appears to contain at least:

```text
DDM
│
├── Header / metadata
│
├── Spatial information
│   ├── bounding sphere
│   └── oriented bounding box
│
├── Object name
│
├── Materials / texture references
│
├── Vertex layout description
│
├── Index / primitive data
│
├── Vertex Buffer #0
│   └── stride 16
│
├── Vertex Buffer #1
│   └── stride 24
│
└── Submesh descriptors
```

The geometry is therefore **not a single simple mesh**. The data strongly
indicates that a DDM can contain multiple submeshes, probably associated with
different materials.

---

# 3. Endianness

The observed structures are generally consistent with **big-endian** integers
and floating-point values.

For example:

```text
3F 80 00 00
```

is interpreted as:

```text
float32 = 1.0
```

Python code must therefore use:

```python
struct.unpack(">f", ...)
struct.unpack(">I", ...)
struct.unpack(">H", ...)
```

rather than the little-endian variants.

---

# 4. Bounding sphere

At offset `0x90`, a `float32` appears to be the bounding-sphere radius.

For `chr300_c01`:

```text
offset 0x90
value ≈ 77.71488
```

The maximum distance from a vertex to the origin is also approximately
`77.71488`.

For `chr310_c01`:

```text
offset 0x90
value ≈ 234.97075
```

The maximum vertex distance from the origin is also approximately `234.97075`.

Conclusion:

```c
float boundingSphereRadius; // offset 0x90
```

is a **very strongly confirmed** hypothesis.

---

# 5. Oriented bounding box / OBB

A particularly interesting structure starts at `0x118`.

For `chr300_c01`:

```text
0x118 = 52.0288277
0x11C = 16.0353088
0x120 =  3.19204235

0x124 = 1.0

0x128 =  0.74058884
0x12C =  0.67195857
0x130 =  0.000019029
0x134 =  0.0000494165

0x138 = -0.8104267
0x13C = 25.727068
0x140 = -0.00120727
```

For `chr310_c01`:

```text
0x118 = 226.15660
0x11C =  69.572235
0x120 =  12.493200

0x124 = 1.0

0x128 = 0.53561175
0x12C = 0.46164921
0x130 = 0.53561181
0x134 = 0.46164921

0x138 = -0.0000000056
0x13C = -2.2879896
0x140 = 13.523168
```

The four values at `0x128`, `0x12C`, `0x130`, and `0x134` form a quaternion
whose norm is approximately 1:

```text
sqrt(qx² + qy² + qz² + qw²) ≈ 1
```

More importantly, applying this quaternion to the vertex positions recovers the
dimensions stored at `0x118`, `0x11C`, and `0x120`, subject to an axis permutation
caused by coordinate-system conventions. This is strong evidence for an
**oriented bounding box**.

Hypothesis:

```c
struct OBB
{
    float extent0;
    float extent1;
    float extent2;

    float unknown;       // = 1.0

    float qx;
    float qy;
    float qz;
    float qw;

    float centerX;
    float centerY;
    float centerZ;
};
```

The final three floats are the **OBB center in object space**. Subtracting this
center from the positions and then applying the conjugate quaternion produces
local bounds equal to `[-extent, +extent]` within approximately `10^-5` for both
models.

### Important

The quaternion must not yet be treated as the model's direct rotation:

```python
mesh.rotation = quaternion
```

Current evidence primarily establishes that it orients a bounding box.

---

# 6. Vertex Stream #0 — stride 16

A first vertex buffer has been identified with a 16-byte stride:

```c
struct Vertex16
{
    packed_snorm_11_11_10 normal;
    packed_snorm_11_11_10 tangent;
    packed_snorm_11_11_10 bitangent;
    half2 constant01;
};
```

The final field is consistently:

```text
0x00003C00
```

and can be interpreted as:

```text
half2 = { 0.0, 1.0 }
```

This is true for every vertex in both reference cases.

---

# 7. 11/11/10 decoding

The first three `uint32` values can be decoded as normalized signed vectors with
an 11-bit, 11-bit, and 10-bit distribution:

```c
X = bits 0..10
Y = bits 11..21
Z = bits 22..31
```

This resembles a conventional packed normal/tangent format. Tests show that:

- the first vector closely matches geometric normals computed from triangles;
- the three vectors are nearly orthogonal;
- results are consistent across both models.

After correctly reconstructing the triangle strips, the dot product between the
stored normal and the average geometric normal has a median of `0.998288` for
`chr300` and `0.999970` for `chr310`. The interpretation is therefore confirmed
on both reference files.

---

# 8. Vertex Stream #1 — stride 24

A second vertex buffer has a 24-byte stride. The current interpretation is:

```c
struct Vertex24
{
    half2 constant01;

    float positionX;
    float positionY;
    float positionZ;

    uint32_t color;

    uint16_t uvU;         // half
    uint16_t uvV;         // half
};
```

In offset form:

```text
+00 uint32
+04 float X
+08 float Y
+0C float Z
+10 RGBA8888
+14 half U
+16 half V
```

The color field is `0xFFFFFFFF` on the models studied so far. UV values are
consistent with a `float16` representation.

---

# 9. Vertex count

```text
chr300_c01: vertex count = 367
chr310_c01: vertex count = 1159
```

Both vertex streams contain exactly these numbers of logical records. The old
1158-record anomaly was caused by a one-byte offset error and incorrect
endianness while reading indices.

---

# 10. Physical vertex-buffer layout

The exact offsets are now established.

For `chr310_c01`:

```text
0x4C7  geometry header: uint32 BE {7, 1159, 1, 2468}
0x4D7  index buffer:    2468 × uint16 BE = 0x1348 bytes
0x181F VertexStream16:  1159 logical 16-byte records
0x608B VertexStream24:  1159 × 24 bytes
0xCD33 end of positions
0xCD37 SubMesh 0
0xCD77 SubMesh 1
```

For `chr300_c01`:

```text
0x32D  geometry header: uint32 BE {7, 367, 1, 948}
0x33D  index buffer:    948 × uint16 BE = 0x768 bytes
0xAA5  VertexStream16:  367 logical 16-byte records
0x2191 VertexStream24:  367 × 24 bytes
0x43F9 end of positions
0x43FD SubMesh 0
```

The `00003C00` word in the last VertexStream16 record is also the first word of
the first VertexStream24 record. The two logical views therefore share the four
bytes at their boundary. This relationship is exact in both files.

---

# 11. Vertex attribute count

The value `0x00000007` appears in the geometry description and most likely means:

```text
vertex_attribute_count = 7
```

This exactly matches the current interpretation:

### Stream 16

```text
1. unknown / half2
2. normal
3. tangent
4. bitangent
```

### Stream 24

```text
5. position
6. color
7. UV
```

Therefore `4 + 3 = 7`, which is a strong correlation.

---

# 12. Materials

Material names and texture references are stored as ASCII strings prefixed by a
big-endian `uint32` length that includes the NUL terminator. The `uint32`
immediately before the first material name stores the material count.

`chr300_c01` has one material:

```text
sword
├── slot 0: chr910_c → file chr910   → normal map
├── slot 1: chr910_u → file chr910_u → diffuse/albedo
└── slot 2: chr910_n → file chr910_n → red specular mask
```

`chr310_c01` has two materials:

```text
mat_ax
├── slot 0: chr930_c01 → file chr930     → auxiliary reflection/matcap
├── slot 1: chr930_n01 → file chr930_n01 → diffuse/albedo
└── slot 2: chr930_f02 → file chr930_f02 → normal map

mat_tar
├── slot 0: chr930_c01 → file chr930     → auxiliary reflection/matcap
├── slot 1: chr930_n01 → file chr930_n01 → diffuse/albedo
└── slot 2: chr930_f01 → file chr930_f01 → auxiliary reflection/matcap
```

References ending in `_c` or `_c01` do not map directly to a physical filename;
they are stored under the base name (`chr910`, `chr930`). The resolver now
handles this rule.

Suffixes do not reliably describe a texture's visual role. The roles above were
determined from decoded XET content. The script detects normal maps from their
blue RGB distribution, detects red masks from their channels, and chooses the
albedo from the remaining detailed textures. Auxiliary textures are exported
and documented but are not yet assigned to a non-standard MTL channel.

## 12.1 Legacy Phong parameters

The material record also contains a sequence of eleven unaligned big-endian
`float32` values. Its absolute offset varies with the texture-slot and shader
records, but the validated sequence is:

```c
float diffuse[3];
float ambient[3];
float specular[3];
float unknown;    // zero in chr300/chr310, nonzero in some variants
float shininess;  // Phong exponent / MTL Ns
```

The converter locates this sequence structurally between the final texture
reference and the next material or geometry header. It does not use offsets
specific to `chr300` or `chr310`.

Observed reference values:

| Material  | Diffuse   | Ambient   | Specular                     | Ns |
| --------- | --------- | --------- | ---------------------------- | -: |
| `sword`   | `1, 1, 1` | `0, 0, 0` | `0.4, 0.4, 0.4`              | 34 |
| `mat_ax`  | `1, 1, 1` | `0, 0, 0` | `0.05, 0.05, 0.05`           | 34 |
| `mat_tar` | `1, 1, 1` | `0, 0, 0` | `0.00018, 0.00017, 0.000175` | 40 |

These are legacy Phong parameters, not metallic/roughness PBR parameters. No
metallic scalar has been identified in the DDM material records. Roughness is
also not stored directly; for diagnostics the converter reports the common
approximation `sqrt(2 / (Ns + 2))`, clearly marked as derived. The original
`Kd`, `Ka`, `Ks`, and `Ns` values are written to MTL without this conversion.

## 12.2 XET data offset

The largest mip always starts at `0x90`. The old converter heuristic incorrectly
added a "pre-mip" area whenever the total size did not match a conventional mip
chain:

```text
chr910_*    : old offset 0x310, incorrect shift 0x280
chr930_f02  : old offset 0x310, incorrect shift 0x280
chr930_n01  : old offset 0xB10, incorrect shift 0xA80
actual offset: 0x90 in every case
```

The payload consists of 8-byte DXT1 blocks, so `0x280` represents 80 blocks. The
decoder therefore started in the middle of a block row, producing a large
horizontal shift and a small vertical shift after wrapping to the next row. This
incorrectly resembled a material UV offset.

The unconventional size concerns the end of the mip chain, not the beginning of
the largest mip. `tools/conversion/xet_to_png.py` now always keeps `0x90` as the
offset and uses size only to distinguish DXT1 from DXT5.

---

# 13. Material / submesh count

A value in the geometry description differs between the two files:

```text
chr300: 1
chr310: 2
```

This exactly matches the apparent material count. Strong hypothesis:

```c
uint32_t materialOrSubmeshCount;
```

or an equivalent field. This agrees with the submesh descriptors found in
`chr310`.

---

# 14. Primitive type

Both submesh structures contain `0x00000004`. In DDM this value represents:

```text
TRIANGLE_STRIP
```

Do not directly apply the OpenGL constant `4 = TRIANGLES`; this format uses a
different enumeration. The structural proof is that each submesh consumes
exactly `primitive_count + 2` indices, which is specific to a triangle strip.
Degenerate triangles connect separate strips.

Conclusion:

```text
primitive_type = 4 = TRIANGLE_STRIP
```

---

# 15. `chr310` SubMesh descriptors

Two `0x40`-byte structures were identified near `0xCD37` and `0xCD77`.

### SubMesh 0

```text
+00 = 1
+04 = 4
+08 = 1989
+0C = 0
+10 = 1005
+14 = 0
...
+38 = 1991
+3C = 1
```

### SubMesh 1

```text
+00 = 1
+04 = 4
+08 = 475
+0C = 1005
+10 = 154
+14 = 1
...
+34 = 1991
```

The following correlation is extremely strong:

```text
SubMesh 0: base vertex = 0,    vertex count = 1005
SubMesh 1: base vertex = 1005, vertex count = 154

1005 + 154 = 1159
```

The current hypothesis is:

```c
+0x04 = primitive_type
+0x0C = base_vertex
+0x10 = vertex_count
+0x14 = material_index
```

This is one of the best-supported parts of the reverse engineering. Field
`+0x34` is `0` and then `1991`, exactly the first indices of the two submeshes,
so its likely role is `first_index`.

---

# 16. Resolved `+0x08` field

Values `1989` and `475` are the primitive counts of the triangle strips,
including degenerate triangles. A submesh index count is therefore:

```text
index_count = primitive_count + 2
```

Exact validation:

```text
chr300: 946 + 2 = 948 indices
chr310: (1989 + 2) + (475 + 2) = 2468 indices
```

---

# 17. Index-buffer structure

For `chr310_c01`, the single index buffer starts at `0x4D7`. The header field is
`0x9A4 = 2468`. This is an **index count**, not a byte size:

```text
2468 × uint16 big-endian = 4936 bytes = 0x1348 bytes
```

Indices are local to each submesh:

```text
SM0: 1991 indices in 0..1004, base_vertex = 0
SM1:  477 indices in 0..153,  base_vertex = 1005
```

The global conversion is therefore
`global_index = base_vertex + local_index`. No index falls outside its submesh
range, and all 2468 indices are consumed.

---

# 18. Resolved false "second index block"

This block does not exist. The previous script interpreted `0x9A4` as a byte
size and then read the remaining bytes as a second buffer. It also read from
`0x4DA` as little-endian. The correct alignment is `0x4D7`, the format is
big-endian `uint16`, and the entire area is one 2468-index buffer.

---

# 19. `chr300_c01` as a control case

`chr300_c01` appears to contain:

```text
vertices = 367
materials/submeshes ≈ 1
primitive type = 4
```

Its submesh descriptor includes:

```text
+04 = 4
+08 = 946
+0C = 0
+10 = 367
+14 = 0
```

Therefore:

```text
base_vertex = 0
vertex_count = 367
material = 0
primitive = 4
```

This confirms that both models share the same general structure. Field
`+0x08 = 946` is the triangle-strip primitive count and implies exactly
`946 + 2 = 948` indices.

---

# 20. Current conceptual model

```text
DDM
│
├── Header
├── Object metadata
├── Bounding sphere
│   └── radius
├── OBB
│   ├── extents
│   ├── quaternion
│   └── center
├── Material descriptors
├── Vertex declaration
│   └── attribute count = 7
├── Index buffer
│   └── big-endian uint16, indices local to each submesh
├── VertexStream16
│   └── stride = 16
│       ├── packed normal
│       ├── packed tangent
│       ├── packed bitangent
│       └── half2 {0, 1}
├── VertexStream24
│   └── stride = 24
│       ├── half2 {0, 1}
│       ├── XYZ
│       ├── RGBA
│       └── UV half2
└── SubMesh descriptors
    ├── primitive type
    ├── primitive count
    ├── base vertex
    ├── vertex count
    ├── material index
    └── additional fields
```

---

# 21. Strongly supported hypotheses

- Big-endian data.
- Bounding-sphere radius at `0x90`.
- OBB extents at `0x118..0x120`.
- OBB quaternion at `0x128..0x134`.
- VertexStream16 stride is 16.
- VertexStream24 stride is 24.
- VertexStream16 stores normal, tangent, and bitangent as 11/11/10 SNORM.
- VertexStream24 stores XYZ, RGBA, and UV.
- Vertex attribute count is 7.
- Primitive type 4 is a triangle strip.
- SubMesh `+0x08` is the primitive count.
- SubMesh `+0x0C` is approximately the base vertex.
- SubMesh `+0x10` is approximately the vertex count.
- SubMesh `+0x14` is approximately the material index.
- The index buffer contains big-endian `uint16` values local to each submesh.

---

# 22. Unresolved hypotheses

## 22.1 Additional SubMesh fields

The following fields still need to be identified:

```text
+0x18 +0x1C +0x20 +0x24 +0x28
+0x2C +0x30 +0x34 +0x38 +0x3C
```

Values of `0xFFFF` probably indicate invalid or optional references. Field
`+0x34` is probably `first_index` (`0`, then `1991` in `chr310`), but it must be
verified on more files.

## 22.2 Actual model transform

Do not yet assume that the DDM stores conventional `position`, `rotation`, and
`scale` fields at the currently identified location. The discovered rotation is
currently proven only as an OBB-related value.

## 22.3 Coordinate units

The decoded position magnitudes strongly suggest centimeter-like source units:

```text
chr300_c01 bounding-sphere radius = 77.714882 source units
chr310_c01 bounding-sphere radius = 234.970749 source units
```

Interpreting those values as centimeters gives radii of approximately `0.777 m`
and `2.350 m`, which are plausible for the sword and large axe. OBJ has no unit
metadata, so `ddm_to_obj.py` multiplies exported positions by `0.01` by default.
The raw coordinates remain unchanged in the decoder and diagnostic CSV; the
scale can be overridden with `--scale` while this hypothesis is tested on more
characters and environment objects.

---

# 23. Next reverse-engineering phase

Submesh validation, triangle-strip reconstruction, the
`submesh → material → textures` relationship, XET conversion, and textured
OBJ/MTL export are now implemented by `tools/conversion/ddm_to_obj.py`.

The next priority is to generalize the parser across:

```text
other characters
weapons
objects
models with more materials
models with more submeshes
```

Unknown descriptor fields, especially those surrounding `first_index`, can then
be correlated across a larger corpus. Once the generic structure is confirmed,
export can progress from OBJ/MTL to glTF, particularly to represent auxiliary
reflection textures correctly.

---

# 24. Guiding principle

Do not consider a field fully decoded only because one value "looks logical."
For every important field, verify at least:

```text
1. consistency with chr300
2. consistency with chr310
3. relationship to physical offsets
4. relationship to sizes
5. relationship to vertices
6. relationship to indices
```

The strongest evidence obtained so far consists of exact relationships:

```text
1159 = 1005 + 154
367 = submesh vertex count
vertex_count × stride = physical vertex-buffer boundaries
```

---

# 25. Very short analysis handoff

```text
DDM = big-endian

chr300_c01 = sword
chr310_c01 = large axe

0x90 = bounding-sphere radius

0x118..0x120 = OBB extents
0x128..0x134 = OBB quaternion
0x138..0x140 = confirmed OBB center

VertexStream16:
    stride 16
    +00 normal 11/11/10 SNORM
    +04 tangent 11/11/10 SNORM
    +08 bitangent 11/11/10 SNORM
    +0C half2 {0, 1}

VertexStream24:
    stride 24
    +00 half2 {0, 1}
    +04 float XYZ
    +10 RGBA8888
    +14 half2 UV

vertex attribute count = 7
primitive type = 4 = triangle strip
index format = big-endian uint16
indices = local to each submesh

chr300:
    vertices = 367
    submeshes = 1
    indices = 948
    primitive_count = 946

chr310:
    vertices = 1159
    materials = 2: mat_ax, mat_tar

chr310 submeshes:
    SM0:
        primitive = 4
        primitive count = 1989
        first index = 0
        index count = 1991
        base vertex = 0
        vertex count = 1005
        material = 0

    SM1:
        primitive = 4
        primitive count = 475
        first index = 1991
        index count = 477
        base vertex = 1005
        vertex count = 154
        material = 1

    1005 + 154 = 1159
    1991 + 477 = 2468 indices

Submesh descriptor size ≈ 0x40
Submesh +0x08 = primitive count
Submesh +0x34 = probably first index

chr310 index buffer:
    offset = 0x4D7
    count = 0x9A4 = 2468 uint16
    byte size = 0x1348

Materials/textures:
    legacy material model = Phong Kd / Ka / Ks / Ns
    no native metallic or roughness field identified
    roughness in analysis.json is derived from Ns
    chr300 sword:
        diffuse = chr910_u
        normal = chr910 (reference chr910_c)
        specular mask = chr910_n
    chr310 mat_ax:
        diffuse = chr930_n01
        normal = chr930_f02
    chr310 mat_tar:
        diffuse = chr930_n01
    chr930 / chr930_f01 = auxiliary reflection textures

NEXT PRIORITY:
    test the structure on more DDM files,
    identify the remaining descriptor fields,
    represent auxiliary textures in a glTF export.
```

# 26. Confidence levels

| Element                            | Confidence        |
| ---------------------------------- | ----------------: |
| Big-endian                         | Very high         |
| Bounding sphere at `0x90`          | Very high         |
| OBB extents/quaternion/center      | Very high         |
| Vertex strides 16 and 24           | Very high         |
| XYZ positions and half-float UVs   | Very high         |
| 11/11/10 normal/tangent/bitangent  | Very high         |
| 7 vertex attributes                | High              |
| Big-endian `uint16` index buffer   | Very high         |
| Primitive `4` = triangle strip     | Very high         |
| `+0x08` = primitive count          | Very high         |
| `+0x0C` = base vertex              | Very high         |
| `+0x10` = vertex count             | Very high         |
| `+0x14` = material index           | High              |
| `+0x34` = first index              | High              |
| Material names/references          | Very high         |
| Legacy Phong Kd/Ka/Ks/Ns           | High              |
| Diffuse/normal/mask roles          | High              |
| Auxiliary reflection textures      | Medium            |
| Centimeter-like coordinate units   | Medium            |
| Actual model transform             | **Undetermined**   |
| Complete header structure          | Partial           |

---

## Experimental decoder status

`tools/conversion/ddm_to_obj.py` now produces:

```text
DDM
 ├─ bounds and OBB validation
 ├─ decoded vertex buffers
 ├─ index buffer partitioned by submesh
 ├─ normals/tangents/bitangents
 ├─ material names and indices
 ├─ legacy Phong Kd/Ka/Ks/Ns and derived roughness metadata
 ├─ resolved XET references converted to PNG
 ├─ JSON/CSV diagnostics
 └─ textured OBJ/MTL grouped by submesh
```

The **minimal geometry decoder works** on `chr300_c01` and `chr310_c01`. The
next step is to generalize it to other DDM variants and provide a richer glTF
export than MTL allows.


## Map GLB export

`map101_R0` has three validated geometry sections and 38 descriptors containing
79,567 vertices and 76,395 nondegenerate-index triangles. The map positions are
already placed in world space. Descriptor +0x28 indexes the 64-entry OBB table
(count at 0xB0, records at 0xB4, stride 0x30): all 38 referenced boxes match the
corresponding submesh vertices. These are bounds, not instance transforms.
Some descriptors cover large spatial batches rather than individual props.

The GLB exporter reconstructs objects by connectivity: original shared vertex
indices and exact shared geometric edges (including material/UV seams). It does
not weld nearby coordinates or merge duplicated vertices touching at one point.
This produces 1,881 nodes / 1,842 distinct meshes for map101_R0. Geometry and
material assignments are retained. Centers are reconstructed AABB centers;
local coordinates plus node translation preserve world positions. Only exactly
identical exported local geometry and materials share mesh data. No original
instance hierarchy, rotations, authoring pivots or semantic object names have
been recovered. Disconnected prop pieces can split; connected props can merge.

GLB embeds resolved diffuse/normal PNGs and uses the source UV orientation
(top-left, unlike the OBJ V flip). Metallic defaults to zero. The mathematical
Phong conversion gives roughness 0.174 for Ns=64 and 0.243 for Ns=32, but this
looks excessively glossy without the original game shader. GLB therefore uses
0.8 by default and records both the exported and derived values in material
extras; a negative `--roughness` restores the derived value.

14,267 map101_R0 triangles are exact overlaps where `gake102__multi` provides
only a normal-map pass over a diffuse surface (including 26 overlaps shared
with another diffuse material). Core glTF cannot reproduce this legacy
multipass shader, and exporting both copies causes z-fighting. The GLB exporter
omits only a coincident normal-only face that has a diffuse counterpart. It
retains standalone normal-only geometry and faces whose attributes differ.
The final GLB contains 62,128 triangles. Auxiliary shader maps are otherwise
not represented.
Specification: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
