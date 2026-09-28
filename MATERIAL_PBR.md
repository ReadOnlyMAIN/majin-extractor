# Matcap and mask to PBR research plan

## Status

The converter provides a deterministic matcap-to-PBR approximation for its
portable glTF output. Highlight coverage estimates perceptual roughness. It no
longer infers metalness from matcap brightness or chroma, because those cannot
distinguish a conductor from a strongly reflecting dielectric. Legacy `Ks` is
instead preserved with `KHR_materials_specular`. The method, confidence, source
texture, and measurements are preserved in material extras.

This remains an approximation: core glTF 2.0 has no native matcap model and a
matcap bakes lighting together with material response. `original-godot` keeps
the matcap shader path and takes roughness from the decoded DDM Phong material.

The standalone research API remains reserved for future calibrated estimators;
the converter's current method is intentionally identified as
`matcap_highlight_perceptual_v2` rather than presented as recovered source data.

## Verified legacy reflection path

Disassembly of the fragment programs embedded in the `KbBase` `fxbf`
containers confirms inline constant relocations for `specularColor` and
`matParam0..2`. The program evaluates an exponent-based specular lobe; it does
not read a native metallic or perceptual-roughness scalar. The DDM material
block supplies specular RGB and a legacy exponent. The portable conversion
therefore keeps two independent quantities:

- `Ns` becomes glTF perceptual roughness through
  `pow(2 / (Ns + 2), 0.25)`;
- `Ks.rgb` becomes dielectric specular strength/color through
  `KHR_materials_specular`;
- metallic remains zero unless future material-specific evidence identifies a
  conductor.

Map materials often reuse `Ns=32` across unrelated surfaces. That is source
authoring granularity, not numerical instability in the converter, so the
export records reduced confidence rather than inventing surface-dependent
roughness values.

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
