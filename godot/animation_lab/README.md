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
The skeleton overlay displays the final imported bone poses. The scene loads
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

The player is now represented by one `chr1` entry backed by `chara/chr1.tscn`;
individual chr1xx packages are excluded from the character dropdown. Selecting
chr1 reveals the right-hand customization panel with head, torso/arms, legs,
and health controls. Other characters hide this panel. Animation playback
continues to use chr100's AnimationPlayer and motion sidecar.

Named clips are shown by default. **Show unnamed segments** includes physical
segments with no source sequence name; stable segment IDs remain in tooltips.
For chr1 this is 391 named clips/aliases, or 477 including 86 unnamed segments.
The corrected player materials, bind-aware jaw retargeting, and root-relative
contamination preview are described in
`research/PLAYER_CONTAMINATION_FINDINGS.md` in the extractor repository.
The original tar shader is still under investigation; the preview remains an
approximation. Source decoding limitations remain in the motion sidecars.
