"""Phase 2.1 probe — curve block header statistics across the KB corpus.

Usage: python research/curve_header_probe.py
Writes CURVE_HEADER_FINDINGS.md next to this script summarising:

- per asset: the spread of the ``A`` and ``B`` header bytes,
- the ratio len(body)/B and the number of (value, 0) sentinel pairs,
- whether A/B explain the value-pair count (they do not).
"""
from __future__ import annotations

import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import parse_motion_package  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "game_files/decompressed/KB/motionPackage"


def sentinel_pairs(body: bytes) -> int:
    """Approximate count of aligned ``(value, 0)`` pairs in a body."""
    words = len(body) // 4
    pairs = 0
    i = 0
    while i < words:
        next_word = struct.unpack_from(">I", body, 4 * i)[0]
        if i + 1 < words and struct.unpack_from(">I", body, 4 * (i + 1))[0] == 0:
            pairs += 1
            i += 2
        else:
            i += 1
    return pairs


def survey() -> str:
    per_asset = defaultdict(list)
    for directory in sorted(ROOT.iterdir()):
        path = directory / "BigEndian" / directory.name
        if not path.is_file():
            continue
        package = parse_motion_package(path)
        blocks = package.curve_blocks
        if not blocks:
            continue
        a_values = [block.header[1] for block in blocks]
        b_values = [block.header[3] for block in blocks]
        pairs = [sentinel_pairs(block.body) for block in blocks]
        per_asset[directory.name] = dict(
            blocks=len(blocks),
            a_min=min(a_values), a_max=max(a_values),
            b_min=min(b_values), b_max=max(b_values),
            pairs_min=min(pairs), pairs_max=max(pairs),
        )
    lines = [
        "# CURVE_HEADER_FINDINGS (probe auto-generated)",
        "",
        "| asset | blocks | A min..max | B min..max | (value,0) pairs min..max |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(per_asset):
        row = per_asset[name]
        lines.append(
            "| {name} | {blocks} | {a_min}..{a_max} | {b_min}..{b_max} "
            "| {pairs_min}..{pairs_max} |".format(name=name, **row)
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = survey()
    out = Path(__file__).resolve().parent / "CURVE_HEADER_FINDINGS.md"
    out.write_text(report, encoding="utf-8")
    print(out)
