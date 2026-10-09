#!/usr/bin/env python3
"""Cross-character probe for generic DDM layout discriminants.

The decoder must pick the right geometry/animation layout for *every* DDM in
the game from bytes alone. This research tool catalogues the header fields,
geometry signatures and skeleton/animation metadata of many characters (and
static maps) so that a byte-level discriminant can be identified instead of a
character-name or hard-coded-offset rule.

It is a read-only diagnostic: nothing here is part of the maintained pipeline.
Run it with no arguments to scan every DDM under ``KB/chara`` and ``KB/map``::

    python research/ddm_variant_probe.py
    python research/ddm_variant_probe.py chr200 chr300 chr301 chr500 chr700
    python research/ddm_variant_probe.py --raw chr301
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "game_files/decompressed/KB"
sys.path.insert(0, str(ROOT / "tools" / "conversion"))

from ddm.skinned import find_skinned_geometry_header  # noqa: E402


def be_u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def fourcc(word: int) -> str:
    """Render a big-endian 4CC the way the engine stores it (byte-reversed)."""
    raw = struct.pack(">I", word)
    return raw.decode("latin-1")


def scan_header(path: Path) -> dict:
    data = path.read_bytes()
    info = {
        "path": str(path),
        "name": path.stem,
        "size": len(data),
        "magic": data[:4].decode("latin-1"),
    }
    if data[:4] != b"\x00ddm":
        info["kind"] = "not-ddm"
        return info
    info["version"] = be_u32(data, 4)
    info["type"] = be_u32(data, 8)
    info["flags"] = be_u32(data, 12)
    info["word_0x10"] = be_u32(data, 0x10)
    info["build_id"] = be_u32(data, 0x28) << 32 | be_u32(data, 0x2C)
    # 0x80 holds a per-file size word and 0x90 the bounding-sphere radius. Both
    # correlate with content and overlap across families, so they are reported
    # for diagnostics only, never used to pick a layout.
    info["word_0x80"] = be_u32(data, 0x80)
    info["radius_0x90"] = struct.unpack_from(">f", data, 0x90)[0]
    skinned = find_skinned_geometry_header(data)
    if skinned:
        info["layout"] = "skinned"
        info["geometry_offset"] = skinned["offset"]
        info["sections"] = skinned["section_count"]
        info["attributes"] = skinned["vertex_attribute_count"]
        info["stride"] = skinned["vertex_stride"]
        info["transform_count"] = be_u32(data, 0xB0) if len(data) >= 0xB4 else None
    else:
        info["layout"] = "static-or-other"
        info["transform_count"] = be_u32(data, 0xB0) if len(data) >= 0xB4 else None
    return info


def scan_all(limit: int | None = None) -> list[dict]:
    rows = []
    for base in (KB / "chara", KB / "map"):
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            candidate = entry / entry.name
            if candidate.is_file() and candidate.read_bytes()[:4] == b"\x00ddm":
                rows.append(scan_header(candidate))
    if limit:
        rows = rows[:limit]
    return rows


def report(rows: list[dict]) -> None:
    print(
        f"{'name':10s} {'size':>9s} {'ver':>3s} {'type':>10s} "
        f"{'flags':>5s} {'w80':>7s} {'rad90':>9s} {'lay':>6s} {'off':>8s} "
        f"{'sec':>4s} {'att':>4s} {'trn':>5s}"
    )
    for r in rows:
        if r.get("kind") == "not-ddm":
            print(f"{r['name']:10s} {r['size']:>9d}  (not ddm)")
            continue
        offset = r.get("geometry_offset")
        print(
            f"{r['name']:10s} {r['size']:>9d} {r.get('version'):>3d} "
            f"0x{r.get('type', 0):08x} {r.get('flags'):>5d} "
            f"{r.get('word_0x80', 0):>7d} "
            f"{r.get('radius_0x90', 0.0):>9.2f} "
            f"{r.get('layout', '?'):>6s} "
            f"{hex(offset) if offset is not None else '-':>8s} "
            f"{r.get('sections', ''):>4} {r.get('attributes', ''):>4} "
            f"{r.get('transform_count', ''):>5}"
        )


def raw_dump(name: str, max_rows: int) -> None:
    """Hexdump the 0x80-byte header of one character DDM."""
    path = KB / "chara" / name / name
    data = path.read_bytes()
    for row in range(0, min(max_rows * 16, len(data), 0x100), 16):
        chunk = data[row:row + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {row:04x} {hexs}  {asc}")


def fourcc_scan(folder: str) -> None:
    """Print the byte-reversed 4CC containers found in ``KB/data`` files."""
    base = KB / "data" / folder
    for path in sorted(base.iterdir()):
        data = path.read_bytes()
        match = re.search(rb"KB/[A-Za-z0-9_/]+", data)
        magic = data[0x80:0x84]
        print(
            f"{path.name:20s} size={len(data):7d} "
            f"hdr4cc={fourcc(be_u32(data, 0x80))!r} "
            f"stored={magic.decode('latin-1')!r} "
            f"first={match.group(0).decode() if match else '-'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="character names (chr300 ...)")
    parser.add_argument("--raw", metavar="NAME", help="hexdump one header")
    parser.add_argument("--fourcc", metavar="FOLDER", help="scan KB/data/<FOLDER>")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.raw:
        raw_dump(args.raw, 8)
        return
    if args.fourcc:
        fourcc_scan(args.fourcc)
        return

    if args.names:
        rows = [
            scan_header(KB / "chara" / name / name)
            for name in args.names
            if (KB / "chara" / name / name).is_file()
        ]
    else:
        rows = scan_all(args.limit)
    report(rows)


if __name__ == "__main__":
    main()
