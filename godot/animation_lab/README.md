# Animation Lab

Copy this directory to `res://animation_lab/` and open
`res://animation_lab/animation_lab.tscn` with **F6**. The scene and its Control
nodes were authored and exercised through the Godot MCP in `majin-godot`.
The reusable custom theme is `theme.tres`.

The viewer recursively discovers imported `.glb` scenes under
`res://original_files/decoded/chara/`. The root node exposes
`characters_directory` and `initial_character` in the Inspector. `chr301` is
the initial example when present; other projects fall back to their first GLB.
An idle/boredom clip is preferred when available. Newly exported characters
appear after the editor imports them and **Refresh characters** is
pressed (restart the running scene after reimporting an already loaded GLB).

Controls are saved nodes, editable in the scene tree:

- Character dropdown, animation name filter and selectable clip list.
- Play/pause, restart, timeline scrubbing, frame/time display and playback rate.
- Loop, skeleton overlay and camera follow switches.
- Camera framing, right-drag orbit and mouse-wheel zoom.

Camera follow is **off by default**. A fixed camera lets source root motion
move the character through the scene; enabling **Follow** follows the animated
root bone. Neither mode edits the animation or removes its root motion.
The skeleton overlay follows the visible body's rig and updates after its
cloth and spring modifiers, including when switching costume or render mode.
The scene loads
Godot's normal imported PackedScenes, including their material post-import
settings, rather than decoding the GLB separately at runtime.

Export another character from the extractor repository:

```sh
python3 tools/conversion/export_folder_to_godot.py \
  game_files/decompressed/KB/chara/chr301 \
  --godot-project /path/to/majin-godot --animations all --ik bake
```

Keep the generated `.glb.import` and `.motion.json` alongside each GLB. For
native Godot materials, install `res://majin_utility/` and select
`res://majin_utility/assign_materials.gd` as the GLB's import script, then
reimport. IK is already baked into the animation tracks; the viewer does not
add another IK solver. Secondary rig controls remain subject to the decoder's
documented limitations.

When exporting into a Godot project, its saved default scene import preset is
applied unchanged. The `majin-godot` preset discards embedded GLB images and
assigns the exported `textures/` resources through the material import script,
avoiding duplicate extracted PNGs beside the GLB.

## Validation on 2026-10-27

Six characters were exported into the user's `majin-godot` project and switched,
paused and scrubbed through their actual UI signals in Godot 4.8-dev7:

| Character | Animations | Bones |
| --- | ---: | ---: |
| chr100 | 477 | 146 |
| chr200 | 369 | 193 |
| chr300 | 150 | 62 |
| chr301 | 140 | 81 |
| chr370 | 38 | 58 |
| chr540 | 83 | 207 |

The filter returned four `walk` matches for chr301, zero for a nonexistent
name, and all 140 clips when cleared. Skeleton overlay drawing, end-of-clip
pause with looping off and the camera modes were checked in the running game:
camera displacement was zero with follow off and 0.52065 m with follow on for
the sampled walking pose. The final run produced no game errors. These are
viewer checks, not proof of fidelity for every source animation.

The player has one `chr1` entry backed by `chara/chr1.tscn`, selected initially
when available. Individual chr1xx/chr72x/chr74x packages, except chr100, are hidden from the
character dropdown. The right panel provides **Body costume**, **Head accessory**,
**Bracelets**, and a **Cinematic rendering** switch enabled by default.

The switch loads the complete cinematic or gameplay body for the same costume.
Gameplay retains that model's own tar geometry. The hidden chr100 animation
master remains active, preserving playback time, equipment and health across
switches. There are eleven body choices and ten models per accessory category,
plus None. Each bracelet half follows its own forearm.

Health and the expandable **Darkness appearance** controls are visible only in
gameplay mode. They provide edge softness, reflections, normals, color, flow,
coverage-mask display and reset. Flow defaults to .1 and the original global tar
textures load automatically. Reset preserves equipment and health. The panel
scrolls on smaller windows and is hidden for other characters.

Named clips are shown by default. **Show unnamed segments** reveals unnamed
physical segments with stable source IDs in tooltips. chr1 retains 391 named
clips/aliases and 86 unnamed segments (477 total). Exact motion/cloth and source
health-curve limits remain documented in the research findings.

`tools/validation/player_render_modes.gd` checks all 22 body/mode combinations,
20 accessories, texture availability and animation preservation in the imported
project. `research/PLAYER_MODEL_USAGE.md` explains the original family selection.

## chr100 upscaled texture pack

**Upscaled textures ×4 (chr100)** switches the displayed instance between
original textures and `res://original_files/decoded/chara/chr100/textures_upscaled/`.
It works with the standalone chr100 entry and chr1's chr100 gameplay body.
Other costumes retain their own textures. The selection survives character,
equipment and render-mode changes; playback is not restarted.

The installed pack contains all 14 original PNG names at four times their
original width and height. c01 (skin/hair) and c02 (clothing/equipment) use
AI-enhanced color atlases, resized to 8192 × 4096; the AI output itself was
1774 × 887, so the saved 8K dimensions do not imply native 8K generated detail.
c03 and the reflection and utility maps use faithful Lanczos resizing.
The four normal maps use AI smoothing followed by Lanczos resizing and
linear RGB vector normalization: n01/n02 are 4096 × 4096, n03/n04 are
2048 × 2048. Their AI sources were 1254 × 1254; final dimensions do not
imply native generated detail at that resolution. Original alpha, import
settings and the DirectX Y convention are retained. The earlier Lanczos-only
normals are backed up in `textures_upscaled/.normals_before_ai/`.
The color atlas alpha channels and all texture import settings come from
the originals. Imported materials are duplicated per instance before texture replacement.
Disabling the pack restores the original texture resources and retains live
shader adjustments such as darkness/health.

AI prompt (built-in imagegen; one call per atlas): faithful UV-atlas remaster;
preserve normalized island coordinates, silhouettes, orientation, colors,
shading and character identity; enhance skin pores, hair and beard for c01,
and cloth weave, stitching, leather and metal for c02; no new motifs,
rearrangement, crop, labels or lighting changes. Generated detail can differ
from the source; use the toggle to compare on the model.

Normal-map AI prompt (built-in imagegen, one edit per n01–n04): smooth pixel
blocks, stair-stepping, compression and speckled noise; preserve UV coordinates,
island placement, seams, feature locations and normal directions; retain the
DirectX RGB convention without a green-channel flip; reconstruct continuous
existing strands and folds; no new motifs, material colors or rendered lighting.
`build_texture_pack.gd.generate_normals()` accepts a `normal_sources` dictionary
mapping original PNG names to generated input paths and normalizes the output
on a worker thread. AI edits may alter fine relief and require on-model review.
