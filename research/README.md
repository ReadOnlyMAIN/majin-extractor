# Research and prototypes

Current motion probes: `transform_layout_probe.py` (4,436-block compact TRS grammar,
DDM rotation anchors and five sequence indices) and `pose_program_probe.py`
(whole-pose/IK sampling; `--all-blocks` covers the full corpus). Evidence is in
[TRANSFORM_LAYOUT_FINDINGS.md](TRANSFORM_LAYOUT_FINDINGS.md). Earlier anatomical,
flat-base and carry interpretations below are historical; archived paths are
not supported commands.

`legacy/` contains historical material that is not part of the maintained
pipeline:

- `ddm_multisection_probe.py`: an older prototype retained for its hypotheses
  about multi-section DDM files and layouts that are not supported yet;
- `asset_inventory.json`: an inventory generated during the initial analysis;
- `README_CONVERSION.md` and `EXTRACTION_SUMMARY.md`: archived guides.

Validated DDM findings should be recorded in `REVERSE_DDM.md` and then
implemented in `tools/conversion/ddm_to_3d.py`.

`tools/research/shader_inspect.py` is the maintained `0bxf/fxbf` inspection
tool. It decodes parameter/sampler reflection records and extracts fragment
programs from the shared RSX microcode pool. Its `--dump-programs` output is in
the original upload byte order expected by RSX fragment disassemblers. JSON
reports also expose each uniform's inline fragment-constant relocation offsets.

`tools/research/rsx_fp_disasm.py` decodes a dumped RSX fragment program into
instructions and inline constants. It is a generic helper used to study the
material shaders (for example the `KbBase` `fxbf` containers) whose sampler
bindings feed the PBR reconstruction.

`motion_probe.py` compares the eight leading "root" scalars of the character
`motionSequence`/`motionPackage` clips across characters and flags the
interpretations that are verified. Its conclusions are recorded in
`MOTION_ANALYSIS.md`:

```bash
python research/motion_probe.py chr300 chr301 chr302 chr303 --clips compare
python research/motion_probe.py chr301 --clips turn
python research/motion_probe.py chr301 --clips identity
python research/motion_probe.py chr300 chr301 chr302 chr303 --clips structure
```

Shared humanoid clips are matched by **name**, not by raw index (chr301 stores
them four slots earlier than chr300/302/303).

`ddm_variant_probe.py` catalogues the DDM header fields, geometry signatures and
skeleton/animation metadata across every character and map DDM. It is used to
find byte-level layout discriminants instead of name/offset rules, and its
findings are recorded in `REVERSE_DDM.md` section 27 ("Generic discriminating
fields"):

```bash
python research/ddm_variant_probe.py            # scan every chara/ + map/ DDM
python research/ddm_variant_probe.py chr200 chr300 chr301 chr500 chr700
python research/ddm_variant_probe.py --raw chr301       # hexdump one header
python research/ddm_variant_probe.py --fourcc data_base # byte-reversed 4CCs
```

`ddm_structural_scan.py` prints the per-file fields that gate the skeleton and
motion handling (`0xB0` transform count, `0xB4` root bone id, skinned
signature):

```bash
python research/ddm_structural_scan.py chara
python research/ddm_structural_scan.py chara map
```

`motion_semantics_probe.py` inspects the animation rig semantics per character:
it prints the motion skeleton's `constraint_flags` histogram, the flagged IK
chain joints and the bind translations. It is how the FK/IK and
rotation/position discriminants in `MOTION_ANALYSIS.md` were found:

```bash
python research/motion_semantics_probe.py chr300 chr301 chr500 chr560
```

`fk_rotation_probe.py` compares FK reconstruction models geometrically. It
maps a selected controller's axes through the independent DDM bind basis and
reports the resulting model-space motion axis; this reproduces the chr30x head
global-Z versus local-X discrepancy without relying on a rendered pose:

```bash
python research/fk_rotation_probe.py chr301 chr302 chr303 --clip 2 --bone-id 110
```

`skin_decode_probe.py` investigates skinned-DDM group detection and the 28-byte
vertex/skin decode. It scores every `u32 == 8` candidate by structural
chaining, the 255-sum weight invariant and palette validity, and validates a
character's decoded skin (positions, UVs, weights, joint indices). It is how the
wrong-group bug on `chr500` and the confirmed skin layout in `REVERSE_DDM.md`
("Skinned group detection") were found:

```bash
python research/skin_decode_probe.py --best chr300 chr500
python research/skin_decode_probe.py --validate chr300 chr301 chr500
python research/skin_decode_probe.py --dump chr500 --offset 0x75a46
```





The `structure` mode reports the scalar-count variants, the triplet binding and
the interleaved auxiliary controls of each character, then verifies that the
bound joint curves of a shared clip are byte-identical across characters once
aligned by action suffix and keyed by bone ID. It also counts how many shared
action clips agree on all common bones, which quantifies how much animation
content the humanoids actually reuse.

Assets whose meaning only exists inside HexaEngine (the procedural day/night
sky, some bespoke engine shaders) are deliberately **not** reverse engineered;
they are reproduced with host-engine primitives (for example a native Godot
`Sky`). See `ROADMAP.md`.


## Decoded PS3 tar shaders

`TAR_SHADER_FORMULA.md` documents the original equations of the 32 compiled
KbTarModel passes. `tools/research/tar_shader_decode.py` decodes both stages,
including skinning loops, predicates and runtime constant bindings;
`tools/research/tar_shader_math.py` contains executable simplified equations
and separate instruction evaluators. The test suite compares 768 RGBA cases
and 128 vertex cases against the original resources when static.pak is present.
The coverage mask is vertex COLOR.r with tarHP cut/fade parameters, not local Y.
Game CPU health mapping and sampler/framebuffer state remain separate inputs.

## Tar rendering

- [TAR_SHADER_FORMULA.md](TAR_SHADER_FORMULA.md): all 32 original shader variants.
- [TAR_RENDER_INTEGRATION.md](TAR_RENDER_INTEGRATION.md): CPU texture bindings,
  Godot shader integration, numerical GPU validation and remaining limits.
- `tools/research/rsx_vp_disasm.py`: RSX vertex instruction decoder.
- `tools/research/tar_shader_decode.py`, `tar_shader_math.py`: decoded programs
  and executable equations checked against an independent instruction evaluator.
- `tools/research/tar_cpu_inspect.py`: bounded evidence from a local decrypted ELF.
- `tools/conversion/tar_assets.py`: exact original global texture extraction.
- `tools/validation/tar_shader_gpu.gd`, `check_tar_gpu.py`: controlled GPU samples
  compared with the independently validated source equations.

[PLAYER_MODEL_USAGE.md](PLAYER_MODEL_USAGE.md) establishes gameplay versus
cinematic player families from original model/item tables and PPU selection
code. `tools/research/player_model_usage.py` reproduces the evidence and records
the original accessory categories separately from the Lab’s inferred mesh slots.
