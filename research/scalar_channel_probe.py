"""Phase 2.2/2.3 probe — scalar-channel projection evidence (no guessing).

Usage: python research/scalar_channel_probe.py [asset ...]
Writes SCALAR_CHANNEL_FINDINGS.md next to this script with, per asset:

- key-extraction status: blocks losslessly decoded by
  ``curves.decode_curve_block`` (modes 5/6/7 carry float payloads).
- value ranges of the float payloads (are they raw pose components?).
- stable anchors: scalar indices whose track is constant AND equals a
  non-zero bind-pose component (zero values excluded as trivially
  matching), tabulated ``scalar_index -> (bone, comp7)``.
- stride statistics: descriptor_count vs 4×bone_count and 7×bone_count.
"""
from __future__ import annotations

import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import parse_motion_package  # noqa: E402
from tools.conversion.motion.curves import (  # noqa: E402
    decode_curve_block,
    curve_header_counts,
)

KB_ROOT = Path(__file__).resolve().parent.parent / "game_files/decompressed/KB"
DEFAULT_ASSETS = ("gim103", "chr300", "chr370", "chr540")
COMP7 = ("T.x", "T.y", "T.z", "R.x", "R.y", "R.z", "R.w")


def survey(assets) -> str:
    lines = [
        "# SCALAR_CHANNEL_FINDINGS (probe auto-generated)",
        "",
    ]
    summary = defaultdict(lambda: dict(blocks=0, ok=0, anchors=Counter(),
                                       payload_min=float("inf"),
                                       payload_max=float("-inf"),
                                       dc_vs_4bones=Counter()))
    for name in assets:
        pkg_path = KB_ROOT / "motionPackage" / name / "BigEndian" / name
        if not pkg_path.is_file():
            lines += [f"## {name}", "", "corpus absent, skipped.", ""]
            continue
        package = parse_motion_package(pkg_path)
        skeleton = package.skeleton
        bones = skeleton.bone_count
        bind_map: dict[int, tuple[int, int]] = {}
        for bone, (trio, quat) in enumerate(
                zip(skeleton.translations, skeleton.rotations)):
            for comp, value in enumerate((*trio, *quat)):
                key = struct.pack(">f", value)
                bind_map.setdefault(key, (bone, comp))

        info = summary[name]
        anchors: dict[int, set] = defaultdict(set)
        for block in package.curve_blocks:
            info["blocks"] += 1
            try:
                decoded = decode_curve_block(block)
            except Exception:
                continue
            info["ok"] += 1
            descriptor_count, _ = curve_header_counts(block)
            info["dc_vs_4bones"][descriptor_count - 4 * bones] += 1
            for track in decoded.tracks:
                if not track.values or track.values[0] != track.values[-1]:
                    continue
                owner = bind_map.get(struct.pack(">f", track.values[0]))
                if owner is None:
                    continue
                assert isinstance(owner, tuple)
                info["payload_min"] = min(info["payload_min"], *track.values)
                info["payload_max"] = max(info["payload_max"], *track.values)
                anchors[track.scalar_index].add(owner)
                info["anchors"][track.scalar_index] += 1

        lines += [
            f"## {name}",
            "",
            f"- bones={bones}; blocks decoded {info['ok']}/{info['blocks']} "
            "by curves.decode_curve_block (lossless scalar storage).",
            f"- descriptor_count − 4×bones histogram: "
            f"{dict(sorted(info['dc_vs_4bones'].items()))}",
        ]
        lines += [
            f"- payload ranges on bind-matching constants: "
            f"[{info['payload_min']:.4f} .. {info['payload_max']:.4f}]",
            "- stable anchors (scalar -> (bone, comp7), non-zero bind):",
        ]
        if not anchors:
            lines.append("  - none")
        for scalar in sorted(anchors):
            owners = sorted(anchors[scalar])
            rendered = ", ".join(
                f"({bone},{COMP7[comp]})" for bone, comp in owners)
            lines.append(f"  - scalar {scalar}: {rendered}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    assets = argv or list(DEFAULT_ASSETS)
    report = survey(assets)
    output = Path(__file__).with_name("SCALAR_CHANNEL_FINDINGS.md")
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
