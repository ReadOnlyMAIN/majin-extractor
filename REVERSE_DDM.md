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

The color field is RGBA8888. It is `0xFFFFFFFF` on the early character
references, but map101 proves that it carries authored data: RGB varies per
vertex and alpha forms terrain-paint masks. In `tikeikusa_`, boundary vertices
are predominantly alpha 0 while interior vertices are predominantly alpha 255,
with intermediate values producing the soft transition. UV values are
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
also not stored directly. Since Godot/glTF use Schlick-GGX, the converter now
matches the half-power angle of the source `cos(theta)^Ns` lobe to the GGX NDF:

```text
c² = 2^(-2/Ns)
alpha² = (1 - c²) / (sqrt(2) - c²)
perceptual_roughness = sqrt(alpha) = pow(alpha², 0.25)
```

This is a model conversion, not a recovered roughness value. `Kd` modulates
Godot albedo. Neutral-grey `Ks` is preserved by `KHR_materials_specular`; for
Godot its luminance scales the conventional dielectric setting, so
`metallic_specular = 0.5 * luminance(Ks)`. Metallic remains zero because
Map101 has neither a conductor flag nor colored conductor reflectance. The
original `Kd`, `Ks`, and `Ns` values are also written to MTL. Since no source
`Ka` has been identified, MTL output reuses `Kd` for `Ka`.

Map101's resulting values are deliberately low-cardinality because the source
itself reuses an authoring template:

| Source values / policy | Godot metallic | Godot specular | GGX roughness | Materials |
| --- | ---: | ---: | ---: | --- |
| shared map-surface policy (`Ks=.8`, `Ns=32`) | 0 | .20 | .5520 | most Map101 materials |
| shared map-surface policy (`Ks=.1`, `Ns=64`) | 0 | .20 | .4709 | `zimen` |
| `Ks=.8`, `Ns=32`, cutout key `0x00843105` | 0 | 0 | 1.0000 | double-sided foliage (`ALFsyokubutuA`, `syokubutuB`) |

The previous exporter ignored `Ks`, leaving Godot's default specular `0.5` on
every material. This especially over-reflected `zimen`, whose source strength
is only `0.1`, and contributed to a wet appearance. It also used a Beckmann
width approximation (`.493`/`.417`) for a GGX renderer; half-power matching
produces the slightly broader, drier `.552`/`.471` lobes above.

The initial Godot mapping used `metallic_specular = 0.5*luminance(Ks)`, hence
`.40` for most records and `.05` for `zimen`. This was a documented model
conversion, not a recovered scale. Visual comparison shows that `.40` remains
too wet and that the `.05`/`.40` discontinuity breaks transitions between the
two-texture `zimen` terrain and neighbouring surfaces. The three decoded
map-surface shader keys therefore use a shared `.20` calibration. It is low but
nonzero, retaining sky/reflection-probe influence. The former `.40` and `.05`
values remain in `specular_from_phong`; raw `Ks` remains in `legacy_phong`.

The cutout foliage override is deliberately keyed to its decoded shader
family, not to texture names or alpha in general. Its `.5520` mathematical
Phong conversion and `.40` specular remain recorded as
`roughness_from_phong` and `specular_from_phong`. The effective export uses
roughness `1`, specular `0`, and metallic `0`: only albedo and cutout opacity
remain. This policy does not affect unrelated alpha-blended terrain paint.

### Material render-state trailer

Map101–Map103 store a nine-byte variant trailer immediately before each
decoded Phong block. Its first byte selects the raster/blend family, followed
by an unaligned four-byte shader feature key and an unresolved word which is
`2` on all Map101 records but can be `0` or `2` on Map102/103:

```text
uint8  render_mode;       // 0 opaque, 1 alpha test, 2 alpha blend/pass
uint32 shader_key;        // feature bits, only grouped so far
uint32 variant_word;      // 2 on map101; 0 or 2 on map102/map103
```

The word is exposed as `render_state.variant_word`; calling it a parameter
count is premature. Both values preserve the same mode/key layout. For
example, Map103's two red-mask `kin` materials use mode 0, key `0x0084733f`,
and word 0, while Map102/103 also contain key `0x00847127` with either word.

The assignments are corroborated by independent asset evidence:

| Map101 materials | Mode | Shader key | Evidence / Godot pipeline |
| --- | ---: | --- | --- |
| `mon`, `isidadami`, pillars, walls, `gake102__base` | 0 | `0x00847125` | opaque; native `StandardMaterial3D` with transparency disabled |
| `ALFsyokubutuA`, `syokubutuB` | 1 | `0x00843105` | cutout foliage; dedicated albedo-only shader, alpha scissor (`0.5`), double-sided rendering and corrected back-face normals |
| `tikeikusa`, `zimenA`, `ALFsyokubutu` | 2 | `0x00807125` | feathered vertex alpha; native classic alpha blend |
| `zimen` | 0 | `0x00847725` | opaque two-albedo/two-normal variant; custom shader blends both pairs with vertex alpha |
| `gake102__multi` | 2 | `0x00847125` | coincident blend pass, folded into the opaque host for Godot |

This corrects the earlier single-shader export, which wrote `ALPHA` for every
material and therefore pushed opaque geometry into Godot's transparent
pipeline. The exact bit-level meaning of `shader_key` remains under study; the
render-mode byte itself is decoded with high confidence.

### Godot native-material mapping

The ordinary opaque and alpha-blend Map101 families do not require handwritten
Godot shaders. The exporter writes `StandardMaterial3D` resources with `albedo_texture`,
`normal_enabled`, `normal_texture`, decoded `Kd`, GGX `roughness`,
`metallic = 0`, decoded relative specular strength, and
`vertex_color_use_as_albedo = true`. Mode 0 leaves `transparency` disabled;
mode 2 selects `TRANSPARENCY_ALPHA`. This retains Godot's native PBR,
depth-prepass and shadow behavior where no source shader distinction requires
custom handling.

The cutout foliage keys `0x00843105`, `0x0082b105`, and `0x0086b105` use
`majin_foliage.gdshader`. The first is the map-material variant; the latter two
occur on reusable `KB/instance` assets, including `ins107..111`. Their textures
contain transparent pixels even though their separate mode byte is zero.
Merely disabling culling leaves the source front normal on back-facing
fragments and can invert their apparent sunlight response. The shader uses
Godot's `FRONT_FACING` fragment input to negate `NORMAL` on the back face,
performs alpha scissor at `0.5`, and fixes metallic/specular/roughness to
`0/0/1`.

Key `0x00847725` is materially different: `zimen` references, in order,
`yuka3_c`, `yuka2_c`, `yuka3_n`, and `yuka2_n`. Its custom shader samples both
albedo/normal pairs and interpolates them with `COLOR.a`; that alpha is an
internal texture weight, not surface transparency. The exact blend polarity is
the current evidence-based interpretation and remains subject to visual
comparison with the original game. A custom shader is also retained for a
folded multipass detail layer such as `gake102`, because its source vertex-alpha
mask cannot be connected directly to `StandardMaterial3D.detail_mask` without
baking another UV texture.

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

Cross-correlation of all detailed Map101/102/103 records and their three L0
files further shows that `Ks` is restricted to neutral-grey `0`, `.1`, `.2`,
or `.8`, while `Ns` is normally `32` or `64`. Map102 `kusa5` contains the
valid disabled pair `Ks=0, Ns=0`; the parser accepts this only when all three
serialized `Ks` components are exactly zero. The same shader key
`0x00847725` also occurs with `.1/64` and `0/32`, proving these are material
inputs rather than values implied by the feature key. All nine L0 materials
use `.8/32` across unrelated rock, vegetation, and architecture, so those
numbers are often exporter presets rather than physically measured values.

Map103's `kin001` and `gim123_kin002` additionally reference red-channel
`*_m` masks under key `0x0084733f`. They are retained as unresolved
surface-response/specular-mask candidates. Their existence is not evidence of
metalness: the associated `Ks` remains neutral `.1`, and the exact mask
operation and polarity have not yet been recovered from that fragment variant.

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

# 27. Generic discriminating fields (cross-character survey)

The decoder must pick the correct layout for **every** DDM in the game from the
bytes alone, never from a file name or a hard-coded offset. This section records
the fields that actually vary between files and whether they can be trusted as
discriminants.

Reproduce the raw survey with:

```bash
python research/ddm_variant_probe.py            # every chara/ + map/ DDM
python research/ddm_structural_scan.py chara    # structural fields per char
python research/ddm_variant_probe.py --fourcc data_base
```

## 27.1 The envelope is *not* a discriminant

Every DDM in `KB/chara` (112 files) and `KB/map` (67 files) shares the exact
same 0x80-byte envelope prefix:

```text
0x00  \0ddm                     magic
0x04  00 00 00 03               version word, always 3
0x08  8f 01 00 33  (or 8f 00 00 33)   type word — see below
0x0C  00 00 00 23               flags word, always 0x23 = 35
0x10  00 00 00 01               constant 1 on every observed file
0x14..0x27                      zero
0x28  <8 bytes>                 build id / content hash — see 27.3
0x80  <uint32>                  per-file size word — see 27.4
0x90  <float32>                 bounding-sphere radius — see 27.4
```

**Confidence: confirmed.** The version word is *never* a usable layout
discriminant: static maps and skinned characters both report `3`. The current
`KNOWN_DDM_VERSIONS` table in `tools/conversion/ddm/binary.py` therefore cannot
separate the two families. Layout selection has to be structural (see 27.5).

## 27.2 Type word `0x8f010033` vs `0x8f000033`

Cross-character counts:

| Group | `0x8f010033` | `0x8f000033` |
| --- | ---: | ---: |
| `chara` | 94 | 18 |
| `map` | 53 | 14 |

The single differing bit is `0x00010000`. The `0x8f000033` group is **not**
build-order ordered (it mixes the old `chr145-156`, `map697/698` with the newer
`chr727-729`, `chr990-992`), so it does not encode a revision. It correlates
with a small, stable set of characters/maps and looks like an **authoring/tool
variant** flag.

**Confidence: confirmed that the field varies; medium for its meaning.** It is a
*reliable discriminator* (byte-exact and stable) but its semantics are unknown.
It must **not** be used to choose the geometry layout: both groups contain
skinned characters and static maps, so the bit is orthogonal to the layout.

## 27.3 Build id at `0x28`

The 8-byte word at `0x28` is unique per file and, sorted across the corpus,
produces a monotonic timeline of when each asset was exported (for example
`map697 < chr905 < ... < map209`). It is almost certainly a build timestamp or
content hash of the export.

**Confidence: high for "per-file build/time id", low for the exact encoding.**
Useful only for diagnostics; it must not gate a layout.

## 27.4 There is *no* reliable header-level family discriminant

An initial hypothesis that a header field (e.g. the word at `0x74`) separates
skinned characters from static maps was **tested and rejected**. The whole
0x00–0x80 header is byte-identical between a skinned character (`chr301`) and a
static map (`map101`) apart from the build id at `0x28`:

```text
0x74  = 0 on every DDM      (not a discriminant)
0x80  = per-file size word   skin 288..43824 (median 29184)
                             stat 288..18624 (median 816)   -- ranges overlap
0x90  = bounding-sphere radius float
                             skin 28..4114    stat 5.6..22648  -- ranges overlap
0xB0  = transform count on skinned DDMs, but OBB/other on maps
```

`0x80` and the radius at `0x90` correlate with *content*, not with the layout
family, and both overlap across families. **Confidence: confirmed by exhausting
the corpus (112 chara + 67 map DDMs).** The static/skinned decision therefore
must remain **structural** — i.e. probe for the skinned geometry signature
(`u32 == 8` at the group header, validated by the section bounds) and fall back
to the map-section detector, exactly as `analyze_file` already does.

The value at `0x80` is still worth documenting as a **sanity gate**: it is
nonzero on every file and looks like a per-file block size (the two families
with `0x80 == 0` were not observed).

## 27.5 The word at `0xB0` is the transform count, at `0xB4` the stored bone-id table

On **skinned** DDMs, `0xB0` is the bind-skeleton transform count (chr300 = 62,
chr301 = 81, chr302 = 43, chr500 = 199). `0xB4` starts a byte-per-bone id table
**stored as little-endian 32-bit words** — see section 28 for the confirmed
storage order. The **raw first byte at `0xB4` is *not* the root bone id**: it is
the fourth id of the first word (again, see section 28).
Corrections to the table below:

- the **real root id is always `0`** once the 4-byte words are un-reversed
  (verified on every tested rig: chr300/301/302/303/500/100/200/320/900/350/380/940/722);
- the raw first byte nevertheless remains a **byte-exact, reliable family
  discriminant** (values `101`, `3`, `102`, `71`, `91`, `11`, `0`) — it names the
  empirically observed rig family, not the root's identity.

| Raw first byte at `0xB4` | Family | Examples |
| ---: | --- | --- |
| `101` (`0x65`) | humanoid v4 | chr300/301/302/303, chr200/2xx, chr500/510, chr800/810/820 |
| `3` (`0x03`) | humanoid variant | chr320/321, chr340/341, chr360/361, chr520/530/540, chr550/560 |
| `102` (`0x66`) | large boss | chr500 |
| `11`, `91`, `71`, `0`, `2` | props/weapons/misc | chr940, chr380, chr350, chr7xx, chr910/930/990 |

**Confidence: confirmed that `0xB0` is the transform count and that the raw
first byte names a rig family; high that the *root id itself* is always `0`.
This is the field to key motion binding on
(matching `MOTION_ANALYSIS.md`, which proves chr300 fails the v4 triplet test).**

A useful derived discriminant is `(transform_count - 8) % 3`:

```text
chr301 = 81  -> (81-8)%3  = 1   (humanoid v4, root scalars + triplets)
chr300 = 62  -> (62-8)%3  = 0   — but MOTION_ANALYSIS shows chr300 is v3
```

The motion side still finds chr300's scalar count `245 % 3 = 2`, i.e. chr300's
**animation** layout is an older one; this confirms the geometry/motion families
are selected by different fields and must be probed independently.

## 27.5b A distinct "2-bone prop" family exists

34 character DDMs share an identical header signature that is **neither** the
skinned-character signature **nor** the map signature. Verified list:

```text
chr700 chr705 chr710 chr715 chr720 chr721 chr723 chr724 chr725 chr726
chr727 chr728 chr729 chr740 chr741 chr742 chr743 chr744 chr745 chr746
chr747 chr748 chr749 chr905 chr910 chr911 chr912 chr920 chr930 chr970
chr980 chr990 chr991 chr992
```

```text
0x80 = 0x120 (288)          fixed size word
0xB0 = 2                    two transforms
0xB4 = 00 00 01 00          bone ids [0, 1]
0xB8 = 00 00 00 ff          parents  [root=0xFF, id 0]
```

A closely related variant (`chr722`, `chr951`, `chr952`, `chr960`) also has
`0xB0 = 2` but `0xB4 = 02 01 00 00`. Under the confirmed 4-byte-word storage
order (section 28) this un-reverses to ids `[0, 0, 1, 2]` — i.e. the true
3-node id list `[0, 1, 2]` plus one pad byte, *not* "ids `[2, 1, 0]`". The
earlier reading simply saw the reversed words.

`find_map_geometry_sections` returns nothing for them and the skinned search
misses them, so they currently fall through to `static-or-other`. They are most
likely **static props with a two-node attachment skeleton** (weapons, effect
emitters), a family the current decoder does not yet handle. Note also that the
standard character skeleton reader fails on some props because the transform
block itself is laid out differently: for `chr950` the arrays read at the
character offsets hold non-unit "quaternions" (`[0, 0, 0, 96.3]`) and garbage
flags — so the prop family needs its own skeleton layout, not a reuse of the
character reader (see `research/` prop probes, planned).

**Confidence: confirmed that these files form a byte-identical-signature group
(34 files + 4 near-variant); medium for the "2-node prop" interpretation.** This
is the highest-value open case for generic coverage: `chr7xx`/`chr9xx` are
common in maps, so a decoder that reports `static-or-other` for them leaves most
placed props unexported.



## 27.6 Byte-reversed 4CC containers in `KB/data`

Every `KB/data/data_base` database file begins at `0x80` with a four-character
code stored **byte-reversed**:

| File | Stored bytes | Read as | Meaning |
| --- | --- | --- | --- |
| `BaseAction` | `TCAB` | `BACT` | Base Action |
| `LinkCharaData` | `DHCL` | `LCHD` | Link Chara Data |
| `LinkCharaDebug` | `BDCL` | `LCDB` | Link Chara Debug |
| `ParamCharaData` | `DHCP` | `PCHD` | Param Chara Data |
| `PathCharaModel` | `MHCP` | `PCHM` | Path Chara Model |
| `PathCharaMotion` | `OMCP` | `PCMO` | Path Chara Motion |
| `PathCharaEquip` | `EHCP` | `PCHE` | Path Chara Equip |

**Confidence: confirmed by inspection of all 19 files.** The engine stores its
4CC labels in reverse byte order. Any future container parser in this project
should reverse the four bytes before comparing against ASCII labels.

## 27.7 The `ENDIAN` path token and the PSX lineage

`KB/data/data_base/PathCharaMotion` and `PathCharaEquip` embed literal
placeholder paths:

```text
KB/motionPackage/<endian>/LittleEndian/      and       /BigEndian/
chr100/ENDIAN/chr100
chr051/ENDIAN/chr051
chr052/ENDIAN/chr052
```

The `ENDIAN` token is substituted at load time with `LittleEndian` or
`BigEndian`, and one of the two directories then provides the curves. On the
shipped PS3 disc only `BigEndian/` exists, but the `LittleEndian` branch and the
`chr051`/`chr052` entries are direct remnants of an earlier, little-endian
platform build (PS2/PSP-era `chr0xx` numbering) that predates the `chr1xx..9xx`
PS3 roster.

**Confidence: high for the placeholder mechanism (the strings are literal and
the substitution target directory exists); medium for the exact prior platform.**
This is strong evidence that the animation format was **ported**, not authored
for the PS3 — which is why the PSX-era interpretation below is worth testing.

## 27.8 PSX-style payload-free angle constants

The animation scalar stream (`motionPackage/<name>/BigEndian/<name>`) encodes
each channel with a 3-bit descriptor. Modes `1..4` carry **no payload** and
decode to a fixed set of quarter-turn angles:

```text
mode 1 -> 0
mode 2 -> +pi/2
mode 3 -> +pi
mode 4 -> -pi/2
```

These are precisely the redundant Euler/quaternion constants a PSX-era (or
PS2-era) quantised rotation encoder emits to avoid storing ±90°/180° values. The
mode histogram confirms they are used heavily on **every** character:

| Character | mode 1 | mode 2 | mode 3 | mode 4 |
| --- | ---: | ---: | ---: | ---: |
| chr300 | 22887 | 10 | 157 | 147 |
| chr301 | 27378 | 138 | 142 | 265 |
| chr302 | 13590 | 152 | 9 | 289 |
| chr500 | 39836 | 70 | 4 | 112 |

**Confidence: confirmed as an encoding fact; high as a ported-format signature.**
Treating these as payload-free constants (already implemented in
`decode_scalar_clip`) is therefore the correct generic interpretation and should
be preserved for any future variant.

## 27.9 `qstm_kind` is a state-semantics discriminant

Each named `qstm` state record carries a kind word at `qstm+0x84`. Its observed
value set differs per character family:

```text
chr301  kinds = {1:3, 2:30, 3:62, 4:14, 5:4, 6:5, 8:2}    (120 named states)
chr500  kinds = {1:11, 2:8, 4:44, 5:12}                    ( 75 named states)
chr560  kinds = {1:3}                                      (  3 named states)
chr900  kinds = {}   (psmr is a 160-byte stub with 3 empty slots, no qstm)
```

**Confidence: confirmed that the sets differ; medium for their meaning.** The
kind word is a good secondary discriminant: a decoder that only handles the
chr30x kind set will silently mis-handle chr500/560 states. The chr900 case is
different in kind — it is a minimal `psmr` with three empty state slots and no
named `qstm` records at all, so a decoder must treat "no named states" as valid
rather than as an error.

## 27.10 Confidence summary for generic selection

| Discriminant | Location | Reliability | Use it for layout selection? |
| --- | --- | --- | --- |
| Version word | `0x04` | Confirmed constant `3` | No — never varies |
| Type word | `0x08` | Confirmed varies (`0x10000` bit) | No — orthogonal to layout |
| Flags word | `0x0C` | Confirmed constant `0x23` | No |
| Build id | `0x28` | Per-file time/hash | No — diagnostics only |
| Header words (`0x74`,`0x80`,`0x90`) | 0x74..0x90 | Confirmed **not** discriminative | No — ranges overlap |
| Transform count | `0xB0` | Confirmed (skinned only) | Yes — skeleton size gate |
| Root bone id | `0xB4` | Confirmed | Yes — rig family |
| Skinned signature | search `u32==8` | Confirmed | **Yes — primary split** |
| 4CC containers | `KB/data` `0x80` | Confirmed (reversed) | Resolves resource paths |
| `ENDIAN` token | `PathCharaMotion` | High | Selects the endian branch |
| Angle modes `1..4` | clip payload | Confirmed | Fixed constant decoding |
| `qstm_kind` | `qstm+0x84` | Confirmed varies | State-family handling |

The recommended generic rule is: **split the family by probing the skinned
geometry signature structurally (there is no header flag), then gate the
skeleton and motion handling on `0xB0`/`0xB4` and the state handling on
`qstm_kind`.** No step depends on the file name.

**Negative results are findings too.** Three tempting discriminants were tested
and rejected on the full corpus: the version word (constant `3`), the type word
(orthogonal to layout), and the header size/radius words (overlapping ranges).
Recording them prevents re-testing them.

---

# 28. Skeleton storage order and motion reference pose (confirmed 2026-10-09)

This section records a cross-validation sweep (chr301, chr300, chr302, chr303,
chr500, chr100, chr200, chr320, chr900, chr350, chr380, chr940, chr722) that
settles three long-standing ambiguities.

## 28.1 DDM bone-id/parent arrays are stored as little-endian 32-bit words

The byte-sized id and parent arrays following `transform_count` are stored
**reversed inside every aligned 4-byte block** (equivalently: little-endian u32
words packing four ids each). Reading them byte-wise as if they were a plain id
list — the previous reading — produces:

- the *first* byte = the id of the fourth transform of the first word, which is
  why the first byte (`101`, `3`, `102`, `71`, `91`, `11`, …) looked like a
  "root id" while the real root id is always `0`;
- "duplicate zero bytes before their final global ids" — those are the word
  padding bytes surfacing at block starts after the reversal.

Proof: un-reversing both arrays reproduces, **byte-exact and on every bundle
tested**, (a) the motion-resource skeleton's bone-id order and (b) the
motion-resource hierarchy's parent ids (5/5 bundles with motion: chr301,
chr300, chr500, chr100, chr200 — and the parent ids additionally match the
un-reversed DDM arrays on the no-motion bundles chr101/chr110/chr800).

```text
stored (word j, LE)      = [id(4j+3), id(4j+2), id(4j+1), id(4j)]
true transform order     = ids un-reversed block-by-block
parents                  = same storage rule; the array occupies
                           padded_count bytes, NOT transform_count bytes
```

## 28.2 The DDM transform arrays are already in the true (motion) order

The translation records (16 bytes each: 3 floats + pad) and the rotation
records (4 floats) pair with the **un-reversed** id array (motion order), not
with the stored byte order:

| Bundle | translation match (un-reversed pairing) | translation match (stored-byte pairing) |
| --- | ---: | ---: |
| chr301 (81) | 81/81 | scrambled — the root id 0 reads `[15.9, 0, 0]` with 20° Z rotation instead of `[0, 98.6, 0.63]` identity |

**Consequence for the current decoder:** `decode_skinned_skeleton`
produces the correct local transforms + hierarchy only when the motion resource
supplies `transform_bone_ids` (34 bundles). For the ~44 skinned bundles
**without** motion resources the pairing is scrambled: every bone receives a
neighbouring bone's local transform (mirrored within each 4-byte word). The
existing skin validation (weight sums, index ranges) cannot see this; the GLB
of such a bundle would show a distorted bind pose. chr101 and chr800 verified:
un-reversed roots are `[0, 98.6, 0.63]` and `[0, 110, 0]` with identity
rotation, exactly the convention of the motion-validated bundles
(chr301 root `[0, 114.54, 0]`).

## 28.3 The motion reference skeleton stores *global* rotations + *local* translations

The 28-byte pose records of the motion package's first segment mix spaces, and
this is now proven rather than assumed:

- **Rotations are accumulated global (model-space) bind rotations.** Applying
  them directly and composing children below them yields a textbook T-pose:
  spine 0→128.9→170.3→182.8→~208 (cm, Y-up), arms straight along ±X at
  shoulder height 160, hip/knee/foot on a vertical line x=±9.98
  (foot y≈7.3, toe y≈1.3). Quaternion pairs match the accumulated DDM local
  chain for 79/81 (chr301), 149/199 (chr500), 73/146 (chr100) joints up to the
  q/−q hemisphere sign; the residual mismatches were artifacts of the old
  truncated parent read (28.1) and they disappear with it.
- **Translations are local** (bone offsets, e.g. chr301 id 2 = `[14.48, 0, 0]`
  inside id 1's frame), and the child offset is rotated by the *parent's global
  rotation*.

The rig itself is **Y-up with bones extending along the parent's +X**: many
bind locals carry the repeated quaternion pattern `(a, b, a, b)` (x=z, y=w)
which composes as `Ry(90°)·Rz(θ)` — the 90° Y pre-rotation turning the limb's
local +X into model +Y. Idle-pose scalar channels hold values ~`1.49–1.61`
(85–92°) on those same joints, consistent with this pattern.

The legacy assumption "*motion quaternions are the DDM joints' local
rotations*" is therefore **retracted**: they are the accumulated globals.

## 28.4 IK chains in the motion skeleton (confirmed)

The hierarchy's flag byte defines explicit IK chains; the segment's second byte
(`ik_chain_count`, chr301 = 4) agrees with the flags:

| Flag bit | Meaning |
| --- | --- |
| `0x1` | chain start (also present on chr500's `0x9` effectors) |
| `0x2` | chain member / end tail (chr301 toe bones, chr500 `0x2` tails) |
| `0x4` | middle joint |
| `0x8` | **effector** — its curve triplet is a model-space *position* |
| `0x40` | class: hand/arm chains (`0x48` effectors) |
| `0x80` | class: leg/foot chains (`0x88` effectors) |

Observed sets: chr301 4 chains (11→13→15, 41→43→45 arms; 200→201→202→203,
210→211→212→213 legs); **chr300 only 3 chains** (one arm lacks IK — plausibly
the sword-holding arm of `chr300_c01`); chr500 4 chains with effectors flagged
`0x9` instead of a class+effector pair. The flags separate the IK *position*
curves from Euler *rotation* curve triplets — a magnitude heuristic cannot do
this (verified: leg effectors reach only 15–35 while unwrapped FK rotations
exceed them).

## 28.5 Per-clip scalar layouts vary (the animation root cause)

The motion package's scalar stream layout is **per-clip, not per-character**.
Within chr301 alone four scalar counts occur (332/334/336/338 for 3/15/46/86
clips; chr300 shows 247/249/251/253), and content positions shift:

- two clips of the **same count** (chr301 clips 0 and 6, both 336) hold the
  same hand-target channel at different scalar positions (+2 apart) and
  different rig-control blocks, so the count word alone does not select a
  layout;
- the channel blocks move by small insertions/omissions at several points
  (e.g. ±2 scalars near the stream head at 8–17, an inserted constant
  `(13.5685, 0.8395, absent)` block, further insertions in the IK tail);
- the value-continuity of adjacent clips (`turn` part A ends exactly where
  part B starts) and the constant anchors (root Y ≈ 112.147, hand/foot IK
  targets, `13.5685/0.8395/34.75/25.389/15.185/9.981`) locate the blocks
  reliably.

The humanoid binder (`infer_humanoid_joint_scalar_starts`) assumes **one
contiguous arithmetic mapping** (`scalar_start = 8 + 3·triplet_k`) for a single
canonical count, and `remap_humanoid_scalar_starts` only shifts whole
positions for count differences 2/4/6. Neither handles mid-stream insertions:
**the maintained decode places the left-hand IK target at scalar 184 (its
cross-clip maximum is the documented 959.54), while the binder's arithmetic
reads scalar 182.** Deriving per-clip binding from the structural anchors above
— replacing the canonical-count heuristic — is the top open implementation
item (`MOTION_ANALYSIS.md` "2026-10-09 findings" and `ROADMAP.md` step 5d).

## 28.6 Root motion channels (confirmed)

Root channels in every clip: `s0` = redundant Y heading projection (0 → ±π on
turn clips, validated), `s1` reserved, `s2/s3/s4` = root position X/Y/Z in model space
(validated by cross-clip continuity: `move_b` delta `(+1.466, +0.400,
−101.310)` equals the `s2..s4` differences), `s5/s6/s7` = bone_000 XYZ Euler
rotation (s6 repeats the heading delta on humanoid turn clips). `boredom`
proves the distinction: s0 is static zero while s5..s7 animate the visible
root lean/twist. A 2-bone prop
(`chr950`, 11 scalars = 8 root channels + 1 child triplet) confirms the
8-channel root block shape `[unknown, unknown, posXYZ, rotXYZ]`.

## 28.7 FK controller axes use the DDM joint-local bind frame

The head-axis defect provides a direct basis test, reproducible with
`research/fk_rotation_probe.py chr301 chr302 chr303 --clip 2 --bone-id 110`.
The first four post-root triplets bind to the shared `1/2/101/110` chain and
are byte-identical across those three rigs. Bone 110 uses scalars `17..19`;
its third component is the dominant animated component in `to_battle`.

Independently, the DDM/motion bind quaternion maps bone 110 local Z to model
`+X` (local X→model `+Z`, local Y→model `-Y`). A global-delta interpretation
retains a large model-Z component `(0.147,-0.850,-0.506)`, reproducing the
lateral head tilt. Applying the controller delta in the local bind frame, with
bone_000's complete animated rotation included, produces
`(0.716,-0.697,0.053)`: strong model-X flexion with almost no lateral Z.
chr302 and chr303 produce the same figures; no character-dependent swizzle is
required.

Confirmed scope: controller components are joint-local, so the old global
delta path is wrong for this chain. Still unresolved: this single-axis test
cannot distinguish pre- from post-multiplication or the six multi-axis Euler
orders. The maintained default is `bind_local * delta`, but it remains marked
experimental until the leg FK slots can be checked against their independent
foot targets.

Large magnitude is not a semantic discriminator. The decoder now labels a
triplet as `position` only when the motion skeleton declares an IK effector.
Large undeclared triplets are preserved as `unknown_controller` and omitted
from FK; unwrapped Euler angles are not silently converted into positions.

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

The converter only auto-detects the `v3` layout (version word `3` at offset 4).
A DDM whose magic is valid but whose geometry layout cannot be auto-detected is
rejected with a structured `UnsupportedDDMVariant` (`version`, `variant`,
`reason`) rather than a generic error, so new variants are reported clearly
instead of being mistaken for corrupt files. Callers can override detection with
`--vertex-count`, `--position-offset`, `--index-offset` and `--index-count` to
force a variant the heuristics miss.


## Map GLB export

`map101` has three validated geometry sections and 38 descriptors containing
79,567 vertices and 76,395 nondegenerate-index triangles. The map positions are
already placed in world space. Descriptor +0x28 indexes the 64-entry OBB table
(count at 0xB0, records at 0xB4, stride 0x30): all 38 referenced boxes match the
corresponding submesh vertices. These are bounds, not instance transforms.
Some descriptors cover large spatial batches rather than individual props.

The GLB exporter reconstructs objects by connectivity: original shared vertex
indices and exact shared geometric edges (including material/UV seams). It does
not weld nearby coordinates or merge duplicated vertices touching at one point.
This produces 1,881 nodes / 1,842 distinct meshes for `map101`. Geometry and
material assignments are retained. Centers are reconstructed AABB centers;
local coordinates plus node translation preserve world positions. Only exactly
identical exported local geometry and materials share mesh data. No original
instance hierarchy, rotations, authoring pivots or semantic object names have
been recovered. Disconnected prop pieces can split; connected props can merge.

GLB embeds resolved diffuse/normal PNGs and uses the source UV orientation
(top-left, unlike the former OBJ V flip). Metallic defaults to zero. An early
conversion incorrectly exported microfacet alpha directly as roughness (0.174
for Ns=64 and 0.243 for Ns=32), which glTF squared again. The next Beckmann
approximation produced `.417`/`.493`; the current conversion instead matches
the source lobe's half-power width to glTF/Godot's GGX, producing `.471`/`.552`.
The method and source values are recorded in material extras; `--roughness`
can still provide an explicit diagnostic override.

The source tangent-space normal maps use the DirectX-style Y− convention. glTF
and Godot 4 consume Y+ tangent-space normals, so the role-aware DDM exporter
inverts the green channel (`G' = 255 - G`) for textures classified as
`normal`. This conversion is recorded as
`directx_y_negative_to_gltf_y_positive` in the texture analysis metadata.
`xet_to_png.py` itself continues to decode the source pixels without semantic
channel conversion.

14,267 `map101` triangles are exact overlaps where `gake102__multi` provides
only a normal-map pass over a diffuse surface (including 26 overlaps shared
with another diffuse material). Core glTF cannot reproduce this legacy
multipass shader, and exporting both copies causes z-fighting. The GLB exporter
omits only a coincident normal-only face that has a diffuse counterpart. It
retains standalone normal-only geometry and faces whose attributes differ.
The final GLB contains 62,128 triangles. Auxiliary shader maps are otherwise
not represented.

### Multipass overlays as Godot detail layers

Map geometry is frequently painted with more than one texture on the *same*
surface. On map101, every `gake102__multi` face (14,267) shares its positions
with a `gake102__base` face, while only 57% of the `base` faces are covered;
`multi` uses a different texture pair (`si_map104_iwa3_c/_n`) than `base`
(`si_map104_yuka3_c/_n`). This is the game's "painted on the 3D" layering: one
surface, two texture sets combined by the shader. Other partial overlaps
(`mon_` over `zimenA_`, `tikeikule_`, `hasiraA_`, `kowarewall_`) are decals with
only 10-37% coverage.

The portable `pbr` GLB cannot express layered materials, so it collapses exact
attribute duplicates to remove z-fighting. The `godot` mode instead detects a
material whose faces are **100% covered** by a strictly larger host. For
map101, the coincident faces also have matching UVs; their differing RGBA
values are intentional, not duplicate noise. The exporter transfers `multi`
vertex alpha to the corresponding `base` vertices, uses it as the
detail-texture/normal blend weight, and omits the coplanar `multi` primitive.
The result is one draw with the source-authored paint mask, without depth bias
or another z-fighting workaround. A coincident layer with different UVs is not
folded until a second-UV representation is implemented.

Map101 also contains non-coincident painted patches (`zimen_`, `tikeikusa_`,
`zimenA_`, and `ALFsyokubutu_`). Their vertex alpha is exported through
`COLOR_0` and consumed directly by the Godot shader. Because this source signal
exists, the older proximity-generated blend maps are disabled for map101.
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
one configured Godot `Material` `.tres` per DDM material, and a JSON
sampler-binding manifest. Relative resource paths keep the generated Godot
folder movable inside a project. Ordinary render families use native
`StandardMaterial3D`; the reconstructed custom shader applies the
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
parallel 16-byte stream supplies normal, tangent and bitangent data. The bind
skeleton is defined by the compact arrays near offset 0xB0 **with the storage
order rules of section 28** (ids/parents as little-endian 32-bit words padded
to a 4-byte multiple, transform arrays already in the true/motion order). The
exporter maps each section's local palette to this global skeleton and writes
glTF `JOINTS_0`, `WEIGHTS_0`, a node hierarchy and inverse bind matrices.

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
62 reference transforms stored as `translation vec3 + quaternion vec4`.
That reference pose matches the DDM bind translations within `3.1e-5` source
units. Per section 28.3, its **quaternions are the accumulated global bind
rotations** (its translations stay local), which is what validates the
skeleton order used by the animation resource; how its variable 247–253
per-clip scalar blocks map onto joints remains open (section 28.5), because
the layout shifts between clips independently of the clip count.

## Skinned group detection and vertex/skin validation

The skinned group header is found by searching the file for the attribute-count
word `u32 == 8`, but that word also appears **inside real vertex data**. Taking
the first match is what made `chr500` fail (`Skinned vertex 0 weights sum to
123`): its first `u32 == 8` is a false positive, not the real group.

### The 28-byte vertex record (confirmed)

```text
+0x00  float32 ×3     position (model space, centimeter-like scale)
+0x0C  uint32         vertex color (0xFFFFFFFF observed)
+0x10  float16 ×2     UV
+0x14  uint8  ×4      local bone-palette indices
+0x18  uint8  ×4      weights, normalized so the four bytes sum to 255
```

Validation over every decoded vertex of every decodable character:

| Character | vertices | finite pos | UV ok | weight sum == 1 | joints resolve |
| --- | ---: | ---: | ---: | ---: | ---: |
| chr300 | 2357 | 2357 | 2357 | 2357 | 2357 |
| chr301 | 3491 | 3491 | 3491 | 3491 | 3491 |
| chr100 | 2998 | 2998 | 2998 | 2998 | 2998 |
| chr200 | 10511 | 10511 | 10511 | 10511 | 10511 |
| chr500 | 22000 | 22000 | 22000 | 22000 | 22000 |
| chr560 | 5693 | 5693 | 5693 | 5693 | 5693 |
| chr370 | 1783 | 1783 | 1783 | 1783 | 1783 |

**Confidence: confirmed.** The interpretation of position/color/UV/palette/weights
is correct for 100% of the vertices: every position is finite, every UV is in
range, every four-weight group sums to 255 (1.0 after normalization), and every
palette index resolves inside the section palette which in turn resolves inside
the skeleton. This is what proves the skin decode, not merely that it does not
raise.

### Section chaining and the 255-sum weight gate

A group is a run of sections, each laid out as:

```text
section header (5 × uint32)   descriptor_count, attributes=8, vertices, palette, indices
index buffer                  indices × uint16
attribute stream              vertices × 16  (11/11/10 normal, tangent, bitangent, marker)
vertex stream                 vertices × 28  (see above)
bone palette                  palette × uint32 (global bone IDs)
submesh descriptors           descriptor_count × (60 bytes + bone_count × uint32)
```

Two structural facts matter for detection:

- The declared **section count is an upper bound, not exact**: `chr500` declares
  10 sections but only 6 chain before the file ends; `chr370` declares 4 but
  chains 3. The real group always chains until the file is filled to within a
  small aligned padding (16 bytes observed).
- Each descriptor's bone-list length is the **third trailing word** `words[14]`
  (offset +0x38), not `words[12]`.

The reliable, name-free detection is therefore: **the first candidate (in file
order) whose first vertex weights sum to 255**. This rejects `chr500`'s false
signature and every stray `u32 == 8`, while selecting the same head the decoder
already used for `chr300/301/...`.

**Confidence: confirmed.** The gate is now enforced in
`find_skinned_geometry_header`; `chr500` changed from a hard failure to 22,000
valid vertices / 23 parts with no change to any other character.

### Multiple meshes per character file

A character file can contain several groups (a main body plus weapons/props).
`chr100` holds a 4-section group at `0x5d91` and a 14-section group at `0xe659`;
`chr500` holds groups at `0x353e4`, `0x75a46` and more. All of them pass the
weight gate and chain to the end (a later group's tail fills the file).

The maintained decoder exports the group it finds first. Decoding **every**
group (to include held weapons) is recorded as an open improvement in
`ROADMAP.md`; it does not affect the skin correctness of the exported group.

### Joint matrix resolution must tolerate cycles (revised 2026-10-09)

`_joint_global_matrices` originally resolved each joint's parent by recursion
and raised `RecursionError` on `chr500`; an iterative resolver with a cycle
guard keeps export terminating. **The cycle itself was an artifact of the
truncated parent read (section 28.1):** with parents read over `padded_count`
bytes and un-reversed per 4-byte word, `chr500`'s corrected hierarchy contains
**zero parent cycles** (re-verified sweep). The guard is now a safety net,
not a workaround for a real rig property.

### Prop-family limbs fail skeleton decode (revised 2026-10-09)

Sweeping every `chara` file: **70 skinned groups decode cleanly** (358,623
vertices validated), 37 files have no skinned group (the "2-bone prop" family),
and 5 raise in `decode_skinned_skeleton` — `chr722`, `chr940`, `chr941`,
`chr942`, `chr950`, all with *invalid quaternion norm* on a low bone index.

The root cause is now identified: the prop family's skeleton block is not laid
out like the character rig reader expects (for `chr950` the offsets read
non-unit "quaternions" such as `[0, 0, 0, 96.3]` and garbage flags — section
27.5b). Additionally the **stored-byte id/parent reading scrambles the bind
pose of any skinned bundle** regardless of this failure (section 28.2), so the
skinnable-but-no-motion bundles (chr101–156, 210–290, 800–830, …) require the
28.1/28.2 storage-order fix even though they "decode cleanly" today.

`research/skin_decode_probe.py` reproduces every check in this section
(`--best`, `--validate`, `--dump`).

### Per-clip scalar layout derives from invariant anchors (§28.6, 2026-10-09)

The humanoid clip stream is not one channel order: whole optional blocks (a
narrower root header, rig-control constant blocks including the known
13.5685/0.8395 and 25.389/9.981 pairs) come and go per clip, and variants of
the *same* scalar count parse different layouts. Value- and mode-signature
based alignment cannot recover which channels moved (animated channels change
value between clips by design), but the constant scalars alone do:

- `collect_constant_anchors` names every constant scalar by
  `(mode, value)`; zero carriers (modes 0/1) and any value key that occurs
  more than once inside the same clip are discarded (ambiguous).
- `build_canonical_anchor_map` stores that list from the canonical-count
  clip; the anchor set survives in every variant, at the position each
  block occupies there.
- `derive_humanoid_scalar_starts` matches canonical anchors to the clip's
  anchors monotonically (nearest position inside a +-10 window), turns the
  position differences into a piecewise shift over canonical offsets, and
  applies it to the verified humanoid binder. Derived starts must stay
  strictly monotonic and inside the clip, otherwise the count-arithmetic
  `remap_humanoid_scalar_starts` fallback serves
  (`per_clip_scalar_layout.kind` records which path ran).

Corpus proof (chr301/302/303, every clip): the sampled IK target triplets
retain the verified effector semantics (feet sample near the ground plane,
hands in the arm-reach range) for 140/140, 150/150 and 149/150 clips versus
136/145/145 under the legacy count arithmetic. While deriving, the canonical
scan also picked up a latent bug: the skeleton segment's leading little-endian
bone count read as a huge big-endian clip count; chr302/303 silently lost the
verified humanoid binder to the generic fallback. The scan now skips the
skeleton segment.

Open: rotation composition (Euler order, absolute vs delta from bind) and the
exact effector offset inside the target block require the FK leg-chain
experiment (plan step 4); the per-clip derivation currently serves the
humanoid binder family only.

### Humanoid leg segment: stride controllers and the effector sextet (§28.7, 2026-10-09)

Decoding one clip's trailing humanoid region scalar by scalar exposes the real
leg-block structure. Per side the segment alternates bound rotation triplets
with whole **stride-controller triplets** whose values are the known invariant
rig constants — observed as `(25.389, 0, -9.981)`, `(0, 15.185, 0)` and
`(34.75, 0, 0)`; the right segment carries an extra stride-controller triplet,
which is why the sinextet of the right foot opens four scalars into the bound
bone-217 slot while the left foot one opens two scalars into bone 204.

Each chain terminates in a contiguous sextet of animated scalars
`[target xyz][orientation xyz]` both in model space (target frame-0 y=7.3 for
the left foot, grounded). The former fixed +2/+3/+5 arithmetic ignored the
interleaved controllers, so on layouts with one stride-controller more or less
the baked "targets" read neighbouring channels (the verified left hand at
scalar 184 was read at 182).

`derive_humanoid_ik_targets(decoded, skeleton, scalar_starts)` implements the
structural scan: it searches `[start, start+8]` for the first offset whose
target triplet and following orientation triplet are both animated (modes
6/7), position-valued and Euler-bounded. `apply_experimental_humanoid_ik`
now uses it per clip with the historical fixed offsets as fallback. Corpus:
the four sextets are found in every canonical-count chr3x clip.

Consequence for the FK experiment: with bound joint triplets correctly
aligned, the remaining FK-vs-foot-target discrepancy (bounded 47..142 cm over
all chr3x clips in the sweep) is dominated by the stride-controller triplets
that the binder reads as leg rotations — the per-bone rule for those slots is
the remaining unknown of plan step 4 (Euler order/composition cannot be
demonstrated until the rotation slot mapping is exact).
