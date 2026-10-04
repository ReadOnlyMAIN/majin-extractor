# Research and prototypes

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

Assets whose meaning only exists inside HexaEngine (the procedural day/night
sky, some bespoke engine shaders) are deliberately **not** reverse engineered;
they are reproduced with host-engine primitives (for example a native Godot
`Sky`). See `ROADMAP.md`.
