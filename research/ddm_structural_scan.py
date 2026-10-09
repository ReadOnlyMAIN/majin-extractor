#!/usr/bin/env python3
"""Focused scan of DDM structural fields used as layout discriminants.

Prints, for every character (and optionally map) DDM, the fields the decoder
could use to choose a layout without a name/offset table: the version word, the
type word, the skeleton word at 0xB0, and whether the skinned geometry
signature is present. Kept small on purpose; the full probe lives in
``ddm_variant_probe.py``.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "conversion"))
KB = ROOT / "game_files/decompressed/KB"

from ddm.skinned import find_skinned_geometry_header  # noqa: E402


def u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def main():
    bases = sys.argv[1:] or ["chara"]
    for base in bases:
        print(f"=== KB/{base} ===")
        print(f"{'name':10s} {'type':>10s} {'B0':>5s} {'B4':>10s} "
              f"{'skin':>4s} {'geom_off':>9s}")
        for entry in sorted((KB / base).iterdir()):
            cand = entry / entry.name
            if not (cand.is_file() and cand.read_bytes()[:4] == b"\x00ddm"):
                continue
            data = cand.read_bytes()
            header = find_skinned_geometry_header(data)
            geo = hex(header["offset"]) if header else "-"
            print(f"{entry.name:10s} 0x{u32(data, 8):08x} {u32(data, 0xB0):>5d} "
                  f"0x{u32(data, 0xB4):08x} {'yes' if header else 'no':>4s} "
                  f"{geo:>9s}")


if __name__ == "__main__":
    main()
