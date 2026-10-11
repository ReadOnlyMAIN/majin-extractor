"""Phase 2 probe — curve header, 3-bit descriptors and clip lengths.

Usage: ``python research/curve_bitstream_probe.py [asset ...]``

The report deliberately stops at demonstrated structure. In particular it
does not assign channel meanings to descriptor codes 0..7 yet.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import (  # noqa: E402
    parse_motion_package,
    parse_motion_sequence,
)
from tools.conversion.motion.curves import (  # noqa: E402
    curve_header_counts,
    descriptor_codes,
)
from tools.conversion.motion.sequence import duration_frames_of  # noqa: E402

KB_ROOT = Path(__file__).resolve().parent.parent / "game_files/decompressed/KB"
DEFAULT_ASSETS = ("gim103", "chr300", "chr370", "chr540", "chr590")


def _fmt_counter(values: Counter) -> str:
    return ", ".join(f"{key}:{values[key]}" for key in sorted(values))


def survey(assets: list[str] | tuple[str, ...]) -> str:
    lines = [
        "# CURVE_BITSTREAM_FINDINGS (probe auto-generated)",
        "",
        "The 4-byte curve header is `u16 descriptor_count, u16 sample_count` "
        "(BE). The body begins with exactly `descriptor_count` 3-bit codes, "
        "read least-significant-first within each BE 24-bit word. "
        "Transform meanings remain open.",
        "",
        "| asset | bones | logical slots | unique blocks | empty/duplicate "
        "slots | descriptors min..max | samples min..max | sample−1 found "
        "in qstm durations |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    details: list[str] = []
    for name in assets:
        package_path = KB_ROOT / "motionPackage" / name / "BigEndian" / name
        sequence_path = KB_ROOT / "motionSequence" / name / name
        package = parse_motion_package(package_path)
        _header, records = parse_motion_sequence(sequence_path)
        durations = {
            value for record in records
            if (value := duration_frames_of(record)) is not None
        }
        headers = [curve_header_counts(block)
                   for block in package.curve_blocks]
        descriptors = [item[0] for item in headers]
        samples = [item[1] for item in headers]
        duration_hits = sum(sample - 1 in durations for sample in samples)
        logical_slots = (package.curve_index.slot_count
                         if package.curve_index else 0)
        empty_slots = sum(slot.size == 0 for slot in package.slots)
        lines.append(
            f"| {name} | {package.skeleton.bone_count} | {logical_slots} | "
            f"{len(package.curve_blocks)} | {empty_slots} | "
            f"{min(descriptors)}..{max(descriptors)} | "
            f"{min(samples)}..{max(samples)} | "
            f"{duration_hits}/{len(samples)} |"
        )

        all_codes = Counter()
        descriptor_mod4 = Counter()
        for block in package.curve_blocks:
            count, _sample_count = curve_header_counts(block)
            descriptor_mod4[count % 4] += 1
            all_codes.update(descriptor_codes(block))
        details.extend([
            "",
            f"## {name}",
            "",
            f"- descriptor count modulo 4: {_fmt_counter(descriptor_mod4)}",
            f"- descriptor code histogram: {_fmt_counter(all_codes)}",
        ])
        if name == "gim103":
            details.append("- exact blocks (`index: descriptors/samples: codes`):")
            details.append("")
            for block in package.curve_blocks:
                count, samples_count = curve_header_counts(block)
                codes = "".join(str(code) for code in descriptor_codes(block))
                details.append(
                    f"  - `{block.block_index}: {count}/{samples_count}: {codes}`"
                )
    return "\n".join(lines + details) + "\n"


def main(argv: list[str]) -> int:
    assets = argv or list(DEFAULT_ASSETS)
    report = survey(assets)
    output = Path(__file__).with_name("CURVE_BITSTREAM_FINDINGS.md")
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
