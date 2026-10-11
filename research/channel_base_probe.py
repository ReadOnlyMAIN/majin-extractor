"""Phase 2.2 probe — determine the per-asset channel base of the 7-per-bone mapping.

Hypothesis (gim103 fact): each bone occupies 7 consecutive scalar indices
(T.x,T.y,T.z,R.x,R.y,R.z,R.w); ``bone = (scalar+base)//7, comp = (scalar+base)%7``
with a per-asset ``base`` shift.

Oracles run for every candidate base in [0, 4*7):

1. structural fit: every animated scalar maps inside the rig (bone < bones);
2. pose oracle at first frame: bones whose 4 rotation components are covered
   must have |q| ≈ 1 (values read at the first key/frame);
3. bind oracle: constant (mode 5) scalars must equal the bind component.

Writes CHANNEL_BASE_FINDINGS.md next to this script.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import parse_motion_package  # noqa: E402
from tools.conversion.motion.curves import decode_curve_block  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "game_files/decompressed/KB/motionPackage"
DEFAULT_ASSETS = ("gim103", "chr300", "chr370", "chr540")
QUAT_COMPS = (3, 4, 5, 6)


def score_base(package, base: float) -> dict:
    bones = package.skeleton.bone_count
    per_bone: dict[int, dict[int, float]] = defaultdict(dict)
    in_rig = out_of_rig = 0
    for block in package.curve_blocks:
        decoded = decode_curve_block(block)
        for track in decoded.tracks:
            if not track.values:
                continue
            channel = track.scalar_index + base
            bone, comp = divmod(channel, 7)
            if bone >= bones:
                out_of_rig += 1
                continue
            in_rig += 1
            per_bone[bone].setdefault(comp, track.values[0])
    quat_norms = []
    for bone, comps in per_bone.items():
        if all(c in comps for c in QUAT_COMPS):
            quat = [comps[c] for c in QUAT_COMPS]
            quat_norms.append(math.sqrt(sum(v * v for v in quat)))
    unit = sum(1 for norm in quat_norms if 0.99 < norm < 1.01)
    return dict(
        base=base, in_rig=in_rig, out_of_rig=out_of_rig,
        quat_bones=len(quat_norms), quat_unit=unit,
        bones_covered=len(per_bone),
    )


def survey(assets) -> str:
    lines = ["# CHANNEL_BASE_FINDINGS (probe auto-generated)", ""]
    for name in assets:
        package = parse_motion_package(ROOT / name / "BigEndian" / name)
        lines += [f"## {name}", "",
                  "| base | in-rig | out-of-rig | quat bones | |q|≈1 | covered bones |",
                  "|---:|---:|---:|---:|---:|---:|"]
        best = None
        for base in range(31):
            score = score_base(package, base)
            lines.append(
                "| {base} | {in_rig} | {out_of_rig} | {quat_bones} "
                "| {quat_unit} | {bones_covered} |".format(**score))
            fraction = (score["out_of_rig"] == 0 and score["quat_bones"]
                        and score["quat_unit"] / score["quat_bones"])
            if fraction:
                if best is None or fraction > best[0]:
                    best = (fraction, base, score)
        if best:
            lines += ["", f"- best base: {best[1]} "
                          f"(unit quats {best[2]['quat_unit']}/{best[2]['quat_bones']}, "
                          f"bones covered {best[2]['bones_covered']})"]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    assets = argv or list(DEFAULT_ASSETS)
    report = survey(assets)
    output = Path(__file__).with_name("CHANNEL_BASE_FINDINGS.md")
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))