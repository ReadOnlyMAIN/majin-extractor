"""Extract observed chr1 SSR cloth/chain parameters for research, not Godot tuning.

The additional-data layout is verified against PS3 reflection registration at
0xb7b0c4..0xb7b4b0. Cloth tables have 256 entries; their exact mapping to solver
cells and the meanings/units of stiffness coefficients still need solver tracing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tools.conversion.cloth_physics import extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("game_files/decompressed/KB/chara/chr100/chr100"))
    parser.add_argument("--resource", type=Path, default=Path("game_files/decompressed/KB/chara/chr100/chr100.resource_29509cc1"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = extract(args.model, args.resource)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"chains": len(report["chains"]), "cloths": [
        {key: cloth[key] for key in ("group", "strand_count", "bones_per_strand", "kt", "ks", "kb", "mass", "air_damping", "update_delta")}
        for cloth in report["cloths"]], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
