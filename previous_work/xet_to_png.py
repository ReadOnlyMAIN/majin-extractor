#!/usr/bin/env python3
"""xet_to_png.py

Game Republic PS3 xet (.bin) -> PNG converter

What this version does
----------------------
- Uses the working xet layout you identified:
  * magic: b'\x00xet'
  * width/height at 0x80 (big-endian u16)
  * texture data starts at 0x90
  * DXT5 blocks are stored color-first, alpha-second
  * mip chain stops at 2x2 (no 1x1 mip in this format)
- Supports single files, folders, and recursive scanning
- Accepts files with or without an extension

Usage
-----
Single file:
    python xet_to_png.py path/to/file.bin --out PNG_OUT

Folder:
    python xet_to_png.py path/to/folder --out PNG_OUT

Recursive:
    python xet_to_png.py path/to/folder --out PNG_OUT --recursive
"""

from __future__ import annotations

import argparse
import os
import struct
from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image

MAGIC = b"\x00xet"
HEADER_SECONDARY_OFFSET = 0x90


# -----------------------------------------------------------------------------
# Size helpers
# -----------------------------------------------------------------------------

def dxt1_size(w: int, h: int) -> int:
    return ((w + 3) // 4) * ((h + 3) // 4) * 8


def dxt5_size(w: int, h: int) -> int:
    return ((w + 3) // 4) * ((h + 3) // 4) * 16


def mip_total(w: int, h: int, bytes_per_block: int) -> int:
    """Total size of the mip chain, stopping at 2x2 (no 1x1 mip here)."""
    total = 0
    ww, hh = w, h
    while ww >= 1 and hh >= 1:
        total += max(1, (ww + 3) // 4) * max(1, (hh + 3) // 4) * bytes_per_block
        if ww == 2 and hh == 2:
            break
        if ww == 1 and hh == 1:
            break
        ww = max(1, ww // 2)
        hh = max(1, hh // 2)
    return total


# -----------------------------------------------------------------------------
# DXT helpers
# -----------------------------------------------------------------------------

def rgb565(c: int) -> Tuple[int, int, int]:
    return (
        ((c >> 11) & 31) * 255 // 31,
        ((c >> 5) & 63) * 255 // 63,
        (c & 31) * 255 // 31,
    )


def decode_alpha_table(a0: int, a1: int) -> List[int]:
    """Standard DXT5 alpha interpolation table."""
    t = [0] * 8
    t[0] = a0
    t[1] = a1
    if a0 > a1:
        t[2] = (6 * a0 + 1 * a1) // 7
        t[3] = (5 * a0 + 2 * a1) // 7
        t[4] = (4 * a0 + 3 * a1) // 7
        t[5] = (3 * a0 + 4 * a1) // 7
        t[6] = (2 * a0 + 5 * a1) // 7
        t[7] = (1 * a0 + 6 * a1) // 7
    else:
        t[2] = (4 * a0 + 1 * a1) // 5
        t[3] = (3 * a0 + 2 * a1) // 5
        t[4] = (2 * a0 + 3 * a1) // 5
        t[5] = (1 * a0 + 4 * a1) // 5
        t[6] = 0
        t[7] = 255
    return t


# -----------------------------------------------------------------------------
# xet layout detection
# -----------------------------------------------------------------------------

def find_offset_and_format(data: bytes, w: int, h: int) -> Tuple[int, str]:
    """Detect the texture data offset and whether DXT1 or DXT5 fits better.

    For the xet layout you found:
    - If the file size matches exactly, data begins at 0x90.
    - Otherwise, there may be pre-mips before the main image.
    """
    filesize = len(data)

    # Exact fits (no pre-mips)
    if filesize == HEADER_SECONDARY_OFFSET + mip_total(w, h, 16):
        return HEADER_SECONDARY_OFFSET, "DXT5"
    if filesize == HEADER_SECONDARY_OFFSET + mip_total(w, h, 8):
        return HEADER_SECONDARY_OFFSET, "DXT1"

    # Otherwise, compute the pre-mip offset (using DXT1-sized pre-mips)
    off = HEADER_SECONDARY_OFFSET
    s = 16
    while s <= max(w, h) // 16:
        off += dxt1_size(s, s)
        s *= 2

    payload = filesize - off
    d1 = abs(payload - mip_total(w, h, 8))
    d5 = abs(payload - mip_total(w, h, 16))
    fmt = "DXT5" if d5 < d1 else "DXT1"
    return off, fmt


# -----------------------------------------------------------------------------
# Decoders
# -----------------------------------------------------------------------------

def decode_dxt1(raw: bytes, w: int, h: int) -> bytes:
    bw = (w + 3) // 4
    bh = (h + 3) // 4
    raw = (raw + b"\x00" * (bw * bh * 8))[: bw * bh * 8]
    out = bytearray(w * h * 4)

    for by in range(bh):
        for bx in range(bw):
            i = (by * bw + bx) * 8
            c0 = struct.unpack_from("<H", raw, i)[0]
            c1 = struct.unpack_from("<H", raw, i + 2)[0]
            bits = struct.unpack_from("<I", raw, i + 4)[0]

            p = [rgb565(c0), rgb565(c1)]
            if c0 > c1:
                p.append(tuple((2 * p[0][j] + p[1][j]) // 3 for j in range(3)))
                p.append(tuple((p[0][j] + 2 * p[1][j]) // 3 for j in range(3)))
            else:
                p.append(tuple((p[0][j] + p[1][j]) // 2 for j in range(3)))
                p.append((0, 0, 0))

            for py in range(4):
                for px in range(4):
                    idx = (bits >> (2 * (4 * py + px))) & 3
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        r, g, b = p[idx]
                        out[o:o + 4] = bytes((r, g, b, 255))

    return bytes(out)


def decode_dxt5_xet(raw: bytes, w: int, h: int) -> bytes:
    """Decode xet DXT5 blocks in the working layout:

    [color block 8 bytes][alpha block 8 bytes]

    Color block layout:
    - bytes 0..1: c0
    - bytes 2..3: c1
    - bytes 4..7: color bits

    Alpha block layout:
    - bytes 8..9: a0, a1
    - bytes 10..15: alpha bits
    """
    bw = (w + 3) // 4
    bh = (h + 3) // 4
    raw = (raw + b"\x00" * (bw * bh * 16))[: bw * bh * 16]
    out = bytearray(w * h * 4)

    for by in range(bh):
        for bx in range(bw):
            i = (by * bw + bx) * 16
            block = raw[i:i + 16]
            if len(block) < 16:
                continue

            # Color-first
            c0 = struct.unpack_from("<H", block, 0)[0]
            c1 = struct.unpack_from("<H", block, 2)[0]
            bits = struct.unpack_from("<I", block, 4)[0]

            # Alpha-second
            a0, a1 = block[8], block[9]
            abits = int.from_bytes(block[10:16], "little")
            alpha = decode_alpha_table(a0, a1)

            p = [rgb565(c0), rgb565(c1)]
            if c0 > c1:
                p.append(tuple((2 * p[0][j] + p[1][j]) // 3 for j in range(3)))
                p.append(tuple((p[0][j] + 2 * p[1][j]) // 3 for j in range(3)))
            else:
                p.append(tuple((p[0][j] + p[1][j]) // 2 for j in range(3)))
                p.append((0, 0, 0))

            for py in range(4):
                for px in range(4):
                    idx = py * 4 + px
                    ci = (bits >> (2 * idx)) & 3
                    ai = (abits >> (3 * idx)) & 7
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < w and y < h:
                        o = (y * w + x) * 4
                        r, g, b = p[ci]
                        out[o:o + 4] = bytes((r, g, b, alpha[ai]))

    return bytes(out)


# -----------------------------------------------------------------------------
# Batch helpers
# -----------------------------------------------------------------------------

def iter_inputs(root: Path, recursive: bool) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    pattern = "**/*" if recursive else "*"
    for p in root.glob(pattern):
        if p.is_file():
            yield p


def known_magic(data: bytes) -> str:
    head = data[:4]
    known = {
        b"\x00ddm": "Modele 3D",
        b"\x00hsc": "Sound cue",
        b"psmr": "Motion",
        b"\x00sme": "Inconnu",
        b"\x00\x00\x00\x00": "Modele/Inconnu",
    }
    return known.get(head, f"inconnu ({head.hex()})")


# -----------------------------------------------------------------------------
# Conversion
# -----------------------------------------------------------------------------

def convert_one(path: Path, out_dir: Path) -> bool:
    data = path.read_bytes()
    if data[:4] != MAGIC:
        print(f"[skip] {path.name}: {known_magic(data)}")
        return False

    if len(data) < 0x84:
        print(f"[skip] {path.name}: dimensions nulles")
        return False

    w = struct.unpack_from(">H", data, 0x80)[0]
    h = struct.unpack_from(">H", data, 0x82)[0]
    if not w or not h:
        print(f"[skip] {path.name}: dimensions nulles")
        return False

    offset, fmt = find_offset_and_format(data, w, h)
    payload = data[offset:]

    if fmt == "DXT5":
        rgba = decode_dxt5_xet(payload[:dxt5_size(w, h)], w, h)
    else:
        rgba = decode_dxt1(payload[:dxt1_size(w, h)], w, h)

    img = Image.frombytes("RGBA", (w, h), rgba)
    bg = Image.new("RGBA", (w, h), (204, 204, 204, 255))
    Image.alpha_composite(bg, img).save(out_dir / (path.stem + ".png"))
    print(f"✓ {path.name} ({w}x{h} {fmt} off=0x{offset:X})")
    return True


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="file or folder")
    ap.add_argument("--out", default="PNG_OUT")
    ap.add_argument("--recursive", action="store_true")
    args = ap.parse_args()

    inp = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if inp.is_dir():
        files = list(iter_inputs(inp, args.recursive))
        ok = 0
        for f in files:
            try:
                if convert_one(f, out_dir):
                    ok += 1
            except Exception as e:
                print(f"✗ {f.name}: {e}")
        print(f"\n{ok}/{len(files)} convertis dans {out_dir}/")
    else:
        convert_one(inp, out_dir)


if __name__ == "__main__":
    main()
