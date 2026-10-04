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
5. export to glTF/GLB (the only supported geometry output);
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
records. Rechecking map101/map102 against the compiled `KbBase` parameter
tables corrected the earlier `Kd/Ka/Ks` interpretation; the validated sequence
is:

```c
float diffuse[3];
float diffuseAlpha;
float unknownAfterDiffuse[2];
float specular[3];
float unknownAfterSpecular;
float shininess;  // legacy specular exponent / MTL Ns
```

There is no evidence for a separate ambient RGB triplet in this block. The old
layout accidentally grouped `diffuseAlpha` and the following two scalars as
ambient color. This did not change the position of `Ks` or `Ns`, but it made
the structural search less reliable and produced invalid MTL ambient colors.

The converter locates this sequence structurally between the final texture
reference and the next material or geometry header. It does not use offsets
specific to `chr300` or `chr310`.

Observed reference values:

| Material  | Diffuse RGBA | Unknown pair | Specular                     | Ns |
| --------- | ------------ | ------------ | ---------------------------- | -: |
| `sword`   | `1, 1, 1, 1` | `0, 0`       | `0.4, 0.4, 0.4`              | 34 |
| `mat_ax`  | `1, 1, 1, 1` | `0, 0`       | `0.05, 0.05, 0.05`           | 34 |
| `mat_tar` | `1, 1, 1, 1` | `0, 0`       | `0.00018, 0.00017, 0.000175` | 40 |

These are legacy Phong parameters, not metallic/roughness PBR parameters. No
metallic scalar has been identified in the DDM material records. Roughness is
also not stored directly; for diagnostics the converter reports the common
microfacet-width approximation `alpha = sqrt(2 / (Ns + 2))`. Because glTF
defines `alpha = roughness²`, the exported perceptual roughness is
`pow(2 / (Ns + 2), 0.25)`, clearly marked as derived. The original
`Kd`, `Ks`, and `Ns` values are written to MTL without this conversion. Since
no source `Ka` has been identified, MTL output reuses `Kd` for `Ka`.

### Compiled-shader verification

The `fxbf` container has a shared fragment-program pool at header word 3. Each
program descriptor stores its fragment offset and byte size at words 9 and 10;
words 7 and 8 describe the separate vertex program. Fragment constants are
inline RSX constant slots patched through relocation lists. For
`KbBaseS_P11_L1`, those lists locate `matParam2`, `matParam1`, `matParam0`, and
`specularColor` in the actual fragment program. The decoded program contains
the expected `LG2`/multiply/`EX2` exponent sequence and applies
`specularColor`, confirming a legacy exponent-based specular lobe rather than a
stored PBR roughness value.

This validates deriving glTF roughness from `Ns`; it does not make that
conversion lossless. On map101/map102, many unrelated surfaces share the same
`Ns=32` authoring template while their `Ks` values differ. glTF preserves that
strength separately through `KHR_materials_specular`. No shader evidence was
found for deriving metalness from these fields, so it remains zero.

The reflection tables use four-byte relocation entries containing a 16-bit
fragment-program byte offset followed by a zero 16-bit reserved field. The
maintained inspector now reports these offsets as
`fragment_constant_offsets`. Representative skinned variants give:

| Program | `matParam2` | `matParam1` | `matParam0` | `specularColor` | `emissionColor` |
| --- | --- | --- | --- | --- | --- |
| `KbBaseS_P11_L1` | `0x10, 0x80` | `0x100` | `0x540` | `0x560` | absent |
| `KbBaseS_P31_L1` | `0x10, 0x80` | `0x110` | `0x640` | `0x7B0` | `0x710, 0x780` |
| `KbBaseS_P33_L1` | `0x40, 0xC0` | `0x150` | `0x250` | `0x4E0` | `0x520` |

In `P31`, unit 4 (`textureSamplerEnvSphere`) is sampled at microcode offset
`0x20`; its RGB is immediately remapped with `value * 2 - 1` and later used in
normalized vector/dot-product calculations. Unit 1 (`textureSamplerUtil`) is
sampled at `0x1D0`. At `0x7A0`, a computed scalar multiplies
`specularColor.rgb` before being added to the lighting result. `P33` likewise
samples units 4, 3 and 1 near the start of the program and treats the unit-4
sample as vector data. Consequently, the current Godot operation
`reflection.rgb * utility` is only a visual approximation; the original shader
does not perform a simple additive matcap-color blend.

The separate `KbShaderParam` resource lists `matParam0`, `matParam1`, and
`matParam2`, but provides no component-level names. Their exact `.x/.y/.z/.w`
authoring meanings therefore remain unresolved; assigning labels such as
“roughness” or “metallic” to those components would currently be speculative.

## 12.2 XET data offset

The largest mip starts at `0x88`. The old converter heuristic incorrectly
added a "pre-mip" area whenever the total size did not match a conventional mip
chain:

```text
chr910_*    : old offset 0x310, incorrect shift 0x288
chr930_f02  : old offset 0x310, incorrect shift 0x288
chr930_n01  : old offset 0xB10, incorrect shift 0xA88
actual offset: 0x88
```

The payload consists of 8-byte DXT1 blocks, so `0x280` represents 80 blocks. The
decoder therefore started in the middle of a block row, producing a large
horizontal shift and a small vertical shift after wrapping to the next row. This
incorrectly resembled a material UV offset.

The unconventional size concerns the end of the mip chain, not the beginning of
the largest mip. `tools/conversion/xet_to_png.py` now keeps `0x88` as the offset
and uses size only to distinguish DXT1 from DXT5. The former `0x90` offset
skipped one 8-byte DXT1 block and produced the approximately four-pixel left
shift visible in `chr300_u01`, `chr300_f`, and `chr300_f02`. File sizes measured
from `0x88` match complete stored mip levels for these textures and for the
`chr910`/`chr930` controls.

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
and `2.350 m`, which are plausible for the sword and large axe. GLB/glTF
positions are in meters, so `ddm_to_3d.py` multiplies exported positions by
`0.01` by default. The raw coordinates remain unchanged in the decoder and
diagnostic CSV; the scale can be overridden with `--scale` while this hypothesis
is tested on more characters and environment objects.

---

# 23. Next reverse-engineering phase

Submesh validation, triangle-strip reconstruction, the
`submesh → material → textures` relationship, XET conversion, and textured
GLB export are now implemented by `tools/conversion/ddm_to_3d.py`.

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
the export can carry auxiliary reflection textures correctly in the GLB.

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

`tools/conversion/ddm_to_3d.py` now produces:

```text
DDM
 ├─ bounds and OBB validation
 ├─ decoded vertex buffers
 ├─ index buffer partitioned by submesh
 ├─ normals/tangents/bitangents
 ├─ character skeletons, joint indices and skin weights
 ├─ material names and indices
 ├─ legacy Phong Kd/Ka/Ks/Ns and derived roughness metadata
 ├─ resolved XET references converted to PNG
 ├─ JSON/CSV diagnostics
 └─ self-contained GLB (glTF 2.0, embedded textures)
```

Static geometry is decoded for weapons such as `chr300_c01` and `chr310_c01`.
The skinned character layout is decoded for the undecorated character DDMs,
including the observed `chr300`, `chr302`, `chr310`, `chr314`, and `chr330`
variants.


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
(top-left, unlike the former OBJ V flip). Metallic defaults to zero. The former
conversion exported microfacet alpha directly as roughness (0.174
for Ns=64 and 0.243 for Ns=32), which glTF squared again and therefore rendered
excessively glossy. GLB now exports perceptual roughness (0.417 and 0.493) and
records both the exported and derived values in material extras; `--roughness`
can still provide an explicit diagnostic override.

The source tangent-space normal maps use the DirectX-style Y− convention. glTF
and Godot 4 consume Y+ tangent-space normals, so the role-aware DDM exporter
inverts the green channel (`G' = 255 - G`) for textures classified as
`normal`. This conversion is recorded as
`directx_y_negative_to_gltf_y_positive` in the texture analysis metadata.
`xet_to_png.py` itself continues to decode the source pixels without semantic
channel conversion.

14,267 map101_R0 triangles are exact overlaps where `gake102__multi` provides
only a normal-map pass over a diffuse surface (including 26 overlaps shared
with another diffuse material). Core glTF cannot reproduce this legacy
multipass shader, and exporting both copies causes z-fighting. The GLB exporter
omits only a coincident normal-only face that has a diffuse counterpart. It
retains standalone normal-only geometry and faces whose attributes differ.
The final GLB contains 62,128 triangles. Auxiliary shader maps are otherwise
not represented.
Specification: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html

### Matcap and red-mask caveat

The content-based texture classifier is not sufficient to establish shader
semantics. On `chr300`, `chr300_f` and `chr300_f02` visually behave like
matcap/reflection lookup textures. Older converter versions promoted the former
to diffuse only because it was larger than `chr300_c01`.
`*_f*` references are now classified as matcaps and excluded from albedo;
`chr300_c01` is the `tar` base color. Core glTF 2.0 has no native matcap
material model, so the matcap remains unrepresented in the GLB.

`chr300_u01` is strongly red-channel dominated. Reflection metadata in the
compiled `KbBase` shaders establishes that the matching four-texture variants
bind `Base=0`, `Util=1`, `Normal=3`, and `EnvSphere=4`. This exactly follows the
`armor_leader` DDM order `c02`, `u01`, `n02`, `f02`, identifying `_u01` as
`textureSamplerUtil` and `_f02` as `textureSamplerEnvSphere`. The matching
programs are `KbBaseS_P31`/`KbBaseNS_P31` and
`KbBaseS_P33`/`KbBaseNS_P33`; the material-selection field has not yet been
decoded far enough to distinguish those two equivalent sampler layouts.
Its red channel closely follows the layout and details of `chr300_c02`, so it is
a surface-response mask using the armor UVs rather than an independent effect.
In game, leader enemies are observed with white armor while ordinary enemies
use the base armor appearance. The `armor_leader` variant adds both
`chr300_u01` and the spherical reflection lookup `chr300_f02` to the shared
`chr300_c02`/`chr300_n02` pair; either the mask, the lookup, or their combination
is therefore a likely source of that white appearance. The sampler bindings are
proven, while the fragment blend operation and mask polarity remain unknown.
It must not yet be wired directly to metallic-roughness channels. The proposed
evidence-based estimation pipeline and output contract live in
`MATERIAL_PBR.md` and `tools/conversion/material_pbr_estimator.py`; no estimator
is active in the converter.

`--material-mode godot` (deprecated alias `original-godot`) preserves the
portable PBR material in the GLB
as a fallback and also writes a Godot 4 spatial shader, every referenced PNG,
one configured `ShaderMaterial` `.tres` per DDM material, and a JSON
sampler-binding manifest. Relative resource paths keep the generated Godot
folder movable inside a project. The reconstructed shader applies the
utility mask to the spherical lookup and exposes strength and polarity controls;
this is an explicit working reconstruction, not yet a byte-exact translation of
the P31/P33 RSX fragment program. Fragment disassembly additionally shows that
the lookup RGB is remapped to signed vector data before the reflection-lighting
calculation, so direct color addition cannot be considered equivalent.

## Skinned character DDM variant

The undecorated `chara/chr300/chr300` file is a valid skinned DDM v3, rather
than a scene/prefab that merely points to `chr300_c01`. It contains three
material records (`tar`, `armor`, and `armor_leader`) and a geometry group at
0x1ADE. Each section begins with:

```text
submesh count
attribute count  = 8
vertex count
bone-palette count
index count
```

The vertex stream uses a 28-byte record containing position, color, half-float
UVs, four local bone-palette indices and four normalized byte weights. A
parallel 16-byte stream supplies normal, tangent and bitangent data. Compact
bone ID, parent ID, translation and quaternion arrays near offset 0xB0 define
the bind skeleton. The exporter maps each section's local palette to this
global skeleton and writes glTF `JOINTS_0`, `WEIGHTS_0`, a node hierarchy and
inverse bind matrices.

For `chr300`, this produces 2,357 source vertices and 62 joints. The `armor`
and `armor_leader` surfaces contain the same 719 faces with alternate
materials; the GLB stores them once and exposes `armor_leader` through
`KHR_materials_variants`, leaving 2,697 distinct triangles. `chr300_c01`
remains the separate 367-vertex sword mesh.

No animation stream is embedded in the character DDM. The matching
`motionSequence/chr300/chr300` resource contains 152 bounded segments: one
skeleton segment, 150 animation clips, and one metadata segment. The matching
`motionPackage/chr300/BigEndian/chr300` contains nine package records. These
external motion resources are detected and included in
the analysis report. Their descriptor tables, key times, constants, linear
samples and value/tangent pairs are decoded with exact byte accounting. The
mapping from those scalar curves to joint transforms is not established, so
they are deliberately not emitted as glTF animation channels yet.

The initial 1,924-byte segment is now decoded independently. For `chr300` it
contains a version-3 header, the 62 ordered bone IDs, hierarchy metadata and
62 local reference transforms stored as `translation vec3 + quaternion vec4`.
That reference pose matches the DDM bind translations within `3.1e-5` source
units. This proves the skeleton order used by the animation resource, but not
yet how its variable 247–253 scalar descriptors map onto transform channels.
