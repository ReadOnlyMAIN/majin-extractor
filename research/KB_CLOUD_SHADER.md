# `KbCloudModel_1` RSX fragment program

This report decodes the 288-byte fragment program embedded in
`KB/shader/KbProcedualCloud` from `static.pak`.

## Record count

The program occupies 18 128-bit slots, but only 14 are instructions. Slots 2,
7, 12, and 17 are inline constants belonging to the preceding instructions.
RSX fragment constants are embedded in the instruction stream.

Reproduce the disassembly with:

```bash
python tools/research/rsx_fp_disasm.py \
  game_files/decompressed/KB/shader/KbProcedualCloud
```

The decoder follows RPCS3's `RSXFragmentProgram.h`, `FPToCFG.cpp`, and
`FragmentProgramDecompiler.cpp`: each four-byte word has its bytes swapped
inside its two 16-bit half-words before the bit fields are interpreted.

## Complete disassembly

```text
00: TEX    H1, TEX0, texture[0]
01: MAD    R1.xyz, H1, CONST.x, CONST.y
02:        inline CONST = (2.0, -1.0, 0.0, 0.0)
03: TEX    H0, TEX1, texture[1]
04: DP3    R0.z, R1, R1
05: DIVSQ  R1.xyz, R1, R0.z
06: MAD    R2.xyz, H0, CONST.x, CONST.y
07:        inline CONST = (2.0, -1.0, 0.0, 0.0)
08: DP3    R0.x, R2, R2
09: ADD    R0.z, -H1.w, H0.w
10: DIVSQ  R2.xyz, R2, R0.x
11: MAD    H0.w, R0.z, CONST.z, H1.w
12:        inline CONST = (0.0, 0.0, 0.0, 0.0)
13: ADD/2  R2.xyz, -R1, R2
14: ADD    H0.xyz, R1, R2
15: NRM/2  H0.xyz, H0.xzy
16: ADD    H0.xyz, -H0, CONST.x
17:        inline CONST = (0.5, 0.0, 0.0, 0.0)
```

`H0` is the fragment color export for this program.

## Equivalent computation

Ignoring FP16 rounding, the program is equivalent to:

```glsl
vec4 sample0 = texture(textureSamplerBase, TEX0.xy);     // unit 0
vec4 sample1 = texture(textureSamplerAmbient, TEX1.xy);  // unit 1

vec3 normal0 = normalize(sample0.rgb * 2.0 - 1.0);
vec3 normal1 = normalize(sample1.rgb * 2.0 - 1.0);
vec3 combined = (normal0 + normal1) * 0.5;
combined = normalize(combined.xzy) * 0.5;

vec4 output_value;
output_value.rgb = vec3(0.5) - combined;
output_value.a = sample0.a;
```

The alpha subtraction in slot 9 is canceled by multiplication with the zero
literal in slot 11. Therefore texture 1 alpha has no effect on the final
result; the final alpha is texture 0 alpha.

## Established texture use

The shader reflection table binds:

| Hardware unit | Cg sampler | Probable game resource | Used channels |
|---|---|---|---|
| 0 | `textureSamplerBase` | `pro_cloud0` | RGB as signed vector; A copied to output |
| 1 | `textureSamplerAmbient` | `pro_cloud1` | RGB as signed vector; A has no net effect |

The association of `pro_cloud0/1` with units 0/1 follows their numeric order
and the two reflected sampler slots. A runtime RSX capture would be required
to prove the resource-to-unit association independently.

All RGB channels are semantically meaningful. They are remapped from `[0, 1]`
to `[-1, 1]`, so displaying them as color naturally looks like colored noise.
They are vector/normal data, not cloud opacity photographs.

## LOD and sampling

Both fetches use plain `TEX`. The program contains no `TXL` (explicit LOD),
`TXB` (bias), or `TXD` (explicit derivatives). Mipmap selection therefore
uses the implicit derivatives of `TEX0` and `TEX1` plus the runtime sampler
state. The bytecode alone cannot establish minification filtering, anisotropy,
wrap mode, or an external sampler LOD bias.

The two inputs use separate interpolated coordinates, `TEX0` and `TEX1`.
Their scrolling/scaling is produced before this fragment program, most likely
by the vertex stage from `pcloudParam*` and `pcloudPos`.

## Consequence for reconstruction

`KbCloudModel_1` does not render the final colored cloud layer. It generates a
combined encoded normal/vector field with alpha from texture 0. The reflected
sun, cloud, shadow, and fog colors are absent from this fragment program.
Those values belong to another stage/pass of the cloud pipeline.

Using `pro_cloud0/1` directly as scalar cloud coverage in Godot is therefore
only an artistic approximation. A closer reconstruction should first reproduce
the vector-combination pass above, then identify the consumer that turns its
RGB normal and alpha coverage into lit cloud color.

