# Player equipment, jaw and contamination

Evidence from the local PS3 resources and connected Godot project, 2026-10-27.

## Jaw: absolute local poses cannot be copied between the two rigs

`KB/chara/chr100/chr100` has 146 bones; the clean `chr101` has 183.
`bone_120` has the same translation (4.03513, .808467, 0 source units) but
its rest quaternion is approximately (0,0,0,1) in chr100 and (1,0,0,0) in
chr101: a 180-degree difference about X. Some other face and arm bones also
have different axes; the extra nodes additionally change the hierarchy.

The previous controller copied the source's absolute parent-local pose,
replacing the target jaw's bind orientation. A check that the two copied poses
were equal therefore missed the defect: equal poses are wrong for different
bind rigs. Transfer the source deformation instead:

```
source_delta = source_global_pose * inverse(source_global_rest)
target_global_pose = source_delta * target_global_rest
```

Convert this result through the target parent's posed inverse to obtain the
local pose. Unmapped intermediary bones retain their local rest transform.
The rest state must leave each target at its own rest, including bone_120.

## Original contamination shader: what is established

Extract the original archive without modifying the game corpus:

```
python tools/extraction/pak_extractor.py game_files/package/PS3_GAME/USRDIR/finalizedPS3/KB/package/static.pak --out /tmp/majin-static --quiet
python tools/research/shader_inspect.py /tmp/majin-static/KB/shader/KbTarModel1 --json
python tools/research/rsx_fp_disasm.py /tmp/majin-static/KB/shader/KbTarModel1
```

The archive contains four dedicated shaders, KbTarModel1..4. Their reflection
names include vertex inputs `rootPos`, `objectBlendOffset`, `uvOffset`, skin
matrices `_localMatrixArray`, fragment uniforms `tarHP` and `tarColor`, and a
separate `textureSamplerTar` on hardware unit 4. Other bindings include base
(unit 0), utility (1), ambient (2), normal (3), environment (5), environment
sphere (6) and shadow (7). These are original binary reflection names, not
inferred Godot parameter names.

For KbTarModel_1, the fragment program is 2,896 bytes at file offset 19,968.
Its first texture instruction samples unit 4 at TEX0.zw, and the result is
remapped with `2*x-1`. This is evidence of a vector/detail input, not evidence
that the tar texture is simply an alpha mask. The program also contains
comparisons, fragment kills and later alpha arithmetic; `tarHP` has constant
relocations at 0x50, 0xA90, 0xAD0 and 0xAF0 (the inspector also reports zero
entries, which should not be interpreted as additional confirmed usages).
The straight-line disassembler does not reconstruct this shader's branches.

The source chr100 DDM has separate T_body and T_huku geometry/materials using
chr100_c03, different normal maps n03/n04, and f01. chr110 uses tar_body and
tar_face; other visually identified pairs have corresponding T* materials.
This supports distinct geometry and a dedicated dynamic tar effect rather
than a single ordinary black material applied to the whole clean outfit.

User observation establishes upward coverage related to health. The shader
names corroborate health-dependent rendering and root-relative controls, but
the exact tarHP vector components, vertex-to-fragment height packing, texture
motion, threshold equation and game health curve are not yet reconstructed.
Do not describe the Godot smoothstep as the recovered original shader.

## Godot preview and remaining limits

The preview draws clean armor from the identified clean package and the
additional tar surfaces from its contaminated partner. Alpha uses a soft
height cutoff. The controller subtracts the animated root's vertical offset
so root-motion jumps do not independently move the coverage threshold through
the body. Height endpoints and feather remain adjustable and health mapping
remains a linear approximation. Original tar shading/noise/UV behavior and
exact equipment seams still need further decoding or comparison with gameplay.

## Animation names

The chr100 package has 86 decoded physical segments for which none of the five
source motionSequence index tables contains a named record. There are zero
cases where these segments have a named record rejected solely by duration.
Thus `segment_XXX` is an explicit fallback, not a corrupt recovered name.
391 exported clips/aliases have verified source names; the total is 477.
Anonymous segments remain available under "Show unnamed segments" in the lab,
with readable labels and their stable original segment IDs in tooltips. They
are not assigned guessed gameplay names or borrowed from adjacent records.
