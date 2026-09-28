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

`KB_CLOUD_SHADER.md` records the complete `KbCloudModel_1` RSX fragment
disassembly, texture-channel semantics, and equivalent vector computation.
