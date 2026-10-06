# Matcap and mask to PBR research plan

## Status

The converter provides a deterministic matcap-to-PBR approximation for its
portable glTF output. Highlight coverage estimates perceptual roughness. It no
longer infers metalness from matcap brightness or chroma, because those cannot
distinguish a conductor from a strongly reflecting dielectric. Legacy `Ks` is
instead preserved with `KHR_materials_specular`. The method, confidence, source
texture, and measurements are preserved in material extras.

This remains an approximation: core glTF 2.0 has no native matcap model and a
matcap bakes lighting together with material response. `godot` keeps
the matcap shader path and takes roughness from the decoded DDM Phong material.

The standalone research API remains reserved for future calibrated estimators;
the converter's current method is intentionally identified as
`matcap_highlight_perceptual_v2` rather than presented as recovered source data.

## Relationship to the DDM blend map

`godot` additionally reconstructs the smooth transitions the original
RSX shader applies between submesh materials. Because the DDM stores one
material per submesh, a direct port shows hard color seams where two submeshes
meet. The exporter measures per-vertex distance to foreign-material faces
(linear falloff over `--blend-radius`, default `0.05` m) and bakes the result
into a UV-space RGBA texture that the generated `.gdshader` samples to
interpolate up to four base-color textures.

This is an approximation in the same sense as the matcap path: the source RSX
arithmetic (mask texture, derivative stencil, or per-fragment code) is not
recoverable from the exported geometry. The blend map reproduces the visible
effect (no hard albedo seam) and is documented as editable output, not as
recovered data. It affects only the base-color path; normals, roughness, the
utility mask, and the matcap lookup are unchanged. See `README.md` for the
generated file layout and the `--blend-radius` tradeoff.

## Verified legacy reflection path

Disassembly of the fragment programs embedded in the `KbBase` `fxbf`
containers confirms inline constant relocations for `specularColor` and
`matParam0..2`. The program evaluates an exponent-based specular lobe; it does
not read a native metallic or perceptual-roughness scalar. The DDM material
block supplies specular RGB and a legacy exponent. The portable conversion
therefore keeps two independent quantities:

- `Ns` becomes perceptual GGX roughness by matching the half-power width of
  `cos(theta)^Ns`: with `c²=2^(-2/Ns)`, `roughness=((1-c²)/(sqrt(2)-c²))^0.25`;
- `Ks.rgb` becomes dielectric specular strength/color through
  `KHR_materials_specular`;
- Godot's scalar `metallic_specular` becomes `0.5*luminance(Ks)`, preserving
  the same dielectric-F0 scaling for Map101's neutral-grey `Ks` values;
- `Kd.rgb` modulates the albedo texture (notably `tikeikusa` has
  `Kd=(1,1,0.1)`);
- metallic remains zero unless future material-specific evidence identifies a
  conductor.

The direct conversion yields roughness `.5520`, specular `.40`, metallic `0`
for Map101's common `Ks=.8, Ns=32` template. `zimen` directly yields roughness
`.4709` and specular `.05` from `Ks=.1, Ns=64`. These specular numbers are kept
as provenance rather than treated as exact Godot values: `Ks` is a legacy
shader input and no linear equivalence with Godot's control has been proven.

For the effective Map101 reconstruction, the decoded map-surface shader keys
`0x00807125`, `0x00847125`, and `0x00847725` share specular `.2`. This retains
some sky/reflection-probe response while avoiding both the wet `.4` appearance
and a reflectance seam between `zimen` and adjacent terrain. The direct values
remain available in `specular_from_phong`.

Map materials often reuse `Ns=32` across unrelated surfaces. That is source
authoring granularity, not numerical instability in the converter, so the
export records reduced confidence rather than inventing surface-dependent
roughness values.

The decoded double-sided cutout foliage family (`alpha_scissor`, shader key
`0x00843105`) is an exception: it reuses the generic `Ks=.8, Ns=32` block but
the original renderer selects a distinct shader variant. The exporter treats
these thin cards as albedo-only diffuse surfaces: smoothness `0` (roughness
`1`), specular `0`, and metallic `0`. It keeps the direct `.5520` roughness and
`.40` specular conversions in `roughness_from_phong` and
`specular_from_phong` for provenance.

## Map101–Map103 binary correlation

The detailed DDMs and their `L0` variants provide 50 decoded material records.
Their legacy specular values are low-cardinality and always neutral grey; no
colored `Ks` was found:

| DDM | `Ks/Ns` populations |
| --- | --- |
| `map101` | 12 × `.8/32`, 1 × `.1/64` |
| `map102` | 7 × `.8/32`, 3 × `.2/32`, 1 × `.1/64`, 1 × `0/0` |
| `map103` | 6 × `.8/32`, 3 × `.2/32`, 2 × `.1/64`, 2 × `.1/32`, 3 × `0/32` |
| three `*_L0` files | 9 × `.8/32` |

This establishes the following source semantics with different confidence
levels:

- `Ks` is a legacy RGB specular-lobe multiplier. The compiled fragment shader
  multiplies its computed highlight by `specularColor`, and Map102/103 contain
  intentional `Ks=0` records. A zero value must therefore remain zero. The
  shared Godot `.2` visual calibration is applied only when source `Ks` is
  nonzero.
- `Ns` is the legacy exponent controlling lobe width. `Ns=0` occurs with
  `Ks=0` on Map102 `kusa5`, where the exponent has no visible effect. `Ns=32`
  is overwhelmingly the exporter default; `64` is a narrower-lobe preset, not
  a stored PBR roughness.
- `Kd` is an albedo tint and is not a roughness clue. Non-white examples are
  reproducible (`map101/tikeikusa = 1,1,.1`, `map102/kusa5 = 1,1,.5`, and
  `map103/kusa5 = 1,1,.135`).
- Metallic remains unsupported by the scalar block. Every map `Ks` is grey,
  including the two gold-colored `kin` materials, so there is no conductor F0
  color or metalness flag in these values.

The same shader key accepts multiple surface-response presets. In particular,
`0x00847725` occurs as `.1/64`, `0/32`, and `0/0`. Consequently neither `Ks`
nor `Ns` may be inferred from the shader key alone. Conversely, exact reused
assets retain their settings: Map101 `zimen` and Map102
`map101_0216_colladafxShader1` share the same four textures, key
`0x00847725`, `Ks=.1`, and `Ns=64`; the `map103_0217_tunagi` material in
Map102/103 retains key `0x00807125`, `.8/32`; and the Map102/103
`map610_0222_ki` tree retains key `0x00843105`, `.8/32`.

The LOD comparison limits how physically precise these values can be. Rock,
grass, architecture, and broken-wall LOD textures all collapse to `.8/32`.
`Ks/Ns` therefore preserve the original renderer's authored response, but in
many records that response is a broad authoring preset rather than a measured
surface property. The half-power `Ns`→GGX conversion remains preferable to an
arbitrary roughness, while its confidence must remain moderate.

Map103 adds a separate per-pixel signal. `kin001` and `gim123_kin002` use key
`0x0084733f`, `Ks=.1`, `Ns=32`, plus red-channel `nm_kin005_m` /
`nm_kin006_m` masks. Their image content follows the decorated surface and is
strongly mask-like. This is credible evidence for a local surface-response
mask, but not yet for its operation or polarity: it could modulate specular
strength, gloss, or another lookup. It must not be connected to metallic or
roughness until that shader variant is disassembled.

The remaining scalars provide no PBR mapping. The two values after diffuse are
`0,0` in every detailed material and in Map102/103 L0; Map101 L0 alone uses
`1,0` while keeping the same `.8/32` preset. The scalar after `Ks` is zero in
all 50 records. This variation rules the first unknown out as the primary
roughness, metallic, or specular-strength value, but does not identify it.
Likewise, diffuse alpha is not treated as a PBR control.

The practical mapping supported by this evidence is therefore:

| Source | Godot/glTF use |
| --- | --- |
| `Kd.rgb` | albedo multiplier |
| `Ks.rgb = 0` | disable specular response |
| `Ks.rgb > 0` | preserve as legacy provenance; use family-calibrated dielectric strength, not a claimed linear conversion |
| `Ns > 0` | half-power conversion to GGX perceptual roughness |
| `Ns = 0` with `Ks = 0` | roughness `1`, specular `0` |
| red `_m` mask on key `0x0084733f` | preserve/classify as an unresolved surface-response mask |
| unknown scalars | preserve only; no PBR connection |
| metallic | `0` pending independent conductor evidence |

Normal-map handedness is handled independently of reflection estimation. DDM
normal textures are converted from source Y− (DirectX style) to the Y+
convention required by glTF/Godot by inverting only their green channel after
material-role classification.

For the reflective `P31/P33` variants, the shader samples
`textureSamplerEnvSphere`, remaps its RGB to a signed vector, and feeds that
vector into normalization and dot-product operations. `textureSamplerUtil`
also enters the lighting path before `specularColor` is applied. The portable
matcap estimator therefore remains deliberately separate from recovered source
semantics: highlight coverage can provide a visual roughness approximation,
but the lookup must not be interpreted as a direct metallic/roughness texture.

## Observations to validate

For `chr300`:

| Material | Texture | Current classifier | Working interpretation |
| --- | --- | --- | --- |
| `tar` | `chr300_c01` | diffuse | base-color swatch |
| `tar` | `chr300_n01` | normal | normal map |
| `tar` | `chr300_f` | matcap | matcap; deliberately excluded from albedo |
| `armor` | `chr300_c02` | diffuse | base-color candidate |
| `armor` | `chr300_n02` | normal | normal map |
| `armor_leader` | `chr300_c02` | diffuse | shared armor base color |
| `armor_leader` | `chr300_u01` | utility mask | bound to `textureSamplerUtil`; exact operation unproven |
| `armor_leader` | `chr300_n02` | normal | shared armor normal map |
| `armor_leader` | `chr300_f02` | matcap | matcap/reflection lookup |

Suffixes are correlations, not a decoded semantic contract. A red channel can
encode intensity, gloss/specular strength, material IDs, or an inverted
roughness-like value. It must not be connected directly to glTF roughness or
metallic channels until cross-model and shader evidence establishes the
mapping and polarity.

## Classifier provenance

Texture roles are assigned by combining a filename-suffix hint (`_c`, `_n`,
`_f`, `_u`) with image content statistics, and each result carries a
`role_source` field recording which evidence decided it:

| `role_source` | Meaning |
| --- | --- |
| `suffix` | Only the filename suffix decided (for example a `_f` matcap, or an `_n`/`_u` hint the content did not confirm). |
| `content` | Only the pixels decided (strongly blue normal, or a red mask with no matching suffix). |
| `suffix+content` | Suffix and content agree (for example `_n` with a blue image). |
| `content+suffix` | A red mask whose `_u` suffix makes it a utility mask rather than a specular mask. |
| `suffix+size` / `size_fallback` | Base-color pick among named candidates / largest remaining auxiliary. |
| `demoted` | A `_c` candidate that lost base-color selection and became auxiliary. |
| `unresolved` | The texture file could not be resolved or converted. |

This keeps the naming heuristics auditable and lets a future calibrated
estimator replace individual rules without hiding why the current role was
chosen.

Leader enemies are observed in game with white armor. Their material variant
adds `chr300_u01` and `chr300_f02` to the ordinary armor's shared base-color and
normal textures. Shader reflection metadata establishes the matching bindings
as `Base=0`, `Util=1`, `Normal=3`, and `EnvSphere=4`, in the same order as the
four DDM texture references. The red channel of `chr300_u01` closely matches the
`c02` UV layout, making it a surface-response utility mask rather than a separate
visual effect. The white result likely comes from its modulation of the
spherical reflection, but the fragment operation and polarity remain to decode.

## Why inversion is difficult

A matcap bakes some combination of illumination, material response, camera
space normal, exposure, and artistic grading into a 2D lookup. Many different
roughness/metallic pairs can produce similar images. A useful estimator will
therefore require declared assumptions and should return confidence and
provenance, not just two scalars.

## Planned pipeline

1. Read material-to-texture associations from `analysis.json`.
2. Identify matcap and scalar-mask candidates without assigning them as albedo.
3. Collect reference samples across characters and known material classes.
4. Determine whether the red mask controls gloss, specular intensity,
   reflectivity, material selection, or another shader input.
5. Fit or search a documented reference lighting/BRDF model against each
   matcap, with bounded roughness and metallic hypotheses.
6. Emit a sidecar JSON containing estimates, confidence, method version,
   evidence paths, and warnings.
7. Only after validation, let `ddm_to_3d.py` opt into those estimates. Preserve
   the source Phong parameters and never overwrite them silently.

## Acceptance criteria

- Matcaps are never selected as base color solely because they are the largest
  remaining texture.
- Roughness polarity is validated against multiple assets.
- Metallic values are supported by evidence beyond image color alone.
- Results are deterministic and covered by fixtures with known expectations.
- Low-confidence estimates remain unset instead of defaulting to a guess.
- The default GLB path remains stable until the estimator is explicitly enabled.
