#!/usr/bin/env python3
"""Extract Game Republic PS3 PAK archives (``\x00kap``).

The format stores raw-deflate streams separated by runs of at least 16 zero
bytes. File names are recovered from the archive index when available.

Examples::

    python tools/extraction/pak_extractor.py file.pak --out DECOMPRESSED
    python tools/extraction/pak_extractor.py package/ --out DECOMPRESSED
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import zlib
from concurrent.futures import Executor, ThreadPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import Iterable


PAK_MAGIC = b"\x00kap"
KB_PREFIX = b"KB/"
NULL_BYTE = b"\x00"
NULL_SEPARATOR = b"\x00" * 16
NULL_RUN = re.compile(rb"\x00{16,}")
INDEX_ENTRY_SIZE = 0x180
MIN_COMPRESSED_SIZE = 8
MIN_OUTPUT_SIZE = 16


def safe_relpath(name: str) -> Path:
    """Return a relative, traversal-free output path."""
    parts = []
    for part in name.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == ".." or ":" in part:
            continue
        parts.append(part)
    return Path(*parts)


def find_separators(data: bytes, start: int) -> list[tuple[int, int]]:
    """Find zero runs using native byte search instead of a Python byte loop.

    ``bytes.find`` locates the next 16-byte marker in optimized C code. A
    regex match, anchored at that known position, only extends the marker to
    the end of its zero run. This preserves the exact boundaries produced by
    the former scanner while being dramatically faster on large archives.
    """
    separators: list[tuple[int, int]] = []
    position = max(0, start)
    data_size = len(data)

    while position < data_size:
        run_start = data.find(NULL_SEPARATOR, position)
        if run_start < 0:
            break

        match = NULL_RUN.match(data, run_start)
        # A 16-byte run was just found, so the anchored match must succeed.
        run_end = match.end() if match is not None else run_start + 16
        separators.append((run_start, run_end))
        position = run_end

    return separators


def parse_index(data: bytes, count: int) -> tuple[list[str], int]:
    """Recover printable ``KB/`` names and the effective index end."""
    declared_index_end = 0x10 + count * INDEX_ENTRY_SIZE
    search_limit = min(declared_index_end * 4, len(data) // 2)
    entries: list[tuple[str, int]] = []
    last_name_offset = 0
    position = 0

    while position < search_limit:
        name_offset = data.find(KB_PREFIX, position, search_limit)
        if name_offset < 0:
            break

        terminator_limit = min(len(data), name_offset + 256)
        terminator = data.find(NULL_BYTE, name_offset, terminator_limit)
        if terminator < 0:
            position = name_offset + len(KB_PREFIX)
            continue

        raw_name = data[name_offset:terminator]
        if (
            name_offset >= 0x20
            and len(raw_name) > 2
            and all(32 <= byte < 127 for byte in raw_name)
        ):
            stored_size = struct.unpack_from(">I", data, name_offset - 0x20)[0]
            entries.append((raw_name.decode("ascii"), stored_size))
            last_name_offset = name_offset

        position = terminator + 1

    names = [name for name, stored_size in entries if stored_size > 0]
    return names, last_name_offset + INDEX_ENTRY_SIZE


def decompress_span(data: bytes, start: int, end: int) -> bytes | None:
    """Decompress one raw-deflate span without copying its compressed bytes."""
    size = end - start
    if size < MIN_COMPRESSED_SIZE:
        return None

    chunk = memoryview(data)[start:end]

    # zlib stops at the end of the deflate stream and ignores the PAK footer.
    # Keeping the trimmed fallback also supports the few malformed variants
    # accepted by the previous extractor.
    try:
        result = zlib.decompress(chunk, wbits=-15)
    except zlib.error:
        if size < 16 + MIN_COMPRESSED_SIZE:
            return None
        try:
            result = zlib.decompress(chunk[:-16], wbits=-15)
        except zlib.error:
            return None

    return result if len(result) > MIN_OUTPUT_SIZE else None


def chunk_boundaries(
    data_size: int,
    index_end: int,
    separators: Iterable[tuple[int, int]],
) -> tuple[list[int], list[int]]:
    separators = list(separators)
    starts = [index_end, *(end for _, end in separators)]
    ends = [*(start for start, _ in separators), data_size]
    return starts, ends


def write_result(
    out_dir: Path,
    names: list[str],
    output_index: int,
    result: bytes,
) -> tuple[str, Path]:
    """Write one decompressed resource and return its display name and path."""
    name = (
        names[output_index]
        if output_index < len(names)
        else f"unknown_{output_index:04d}"
    )
    relative_path = safe_relpath(name)
    if not relative_path.parts:
        relative_path = Path(f"unknown_{output_index:04d}")

    output_path = out_dir / relative_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result)
    return name, output_path


def extract_pak(
    pak_path: Path,
    out_dir: Path,
    executor: Executor,
    *,
    quiet: bool = False,
) -> int:
    """Extract one archive using a reusable decompression executor."""
    data = pak_path.read_bytes()
    if len(data) < 0x10:
        raise ValueError("PAK trop court")
    if data[:4] != PAK_MAGIC:
        raise ValueError(f"invalid PAK magic: {data[:4].hex()}")

    count = struct.unpack_from(">I", data, 8)[0]
    declared_index_end = 0x10 + count * INDEX_ENTRY_SIZE
    names, index_end = parse_index(data, count)
    separators = find_separators(data, index_end)

    if not quiet:
        print(f"Pak: {pak_path.name} ({len(data)} bytes)")
        print(
            f"Count header: {count}  "
            f"Index principal: 0x{declared_index_end:X}"
        )
        print(f"Valid names: {len(names)}  Actual index end: 0x{index_end:X}")
        print(f"Separators: {len(separators)}")

    if not separators:
        print(f"[WARN] {pak_path.name}: no separator found")
        return 0

    starts, ends = chunk_boundaries(len(data), index_end, separators)
    results = executor.map(decompress_span, repeat(data), starts, ends)

    extracted = 0
    for result in results:
        if result is None:
            continue

        name, _ = write_result(out_dir, names, extracted, result)
        if not quiet:
            print(
                f"[OK] [{extracted}] {name}  "
                f"({len(result)} bytes) magic={result[:4].hex()}"
            )
        extracted += 1

    missing = names[extracted:]
    if not quiet:
        for name in missing:
            print(f"[FAIL] {name}: no matching chunk")
        print(f"{extracted} OK  {len(missing)} unmapped")
    else:
        print(f"{pak_path.name}: {extracted} extracted, {len(missing)} unmapped")

    return extracted


def default_worker_count() -> int:
    """Match Python's practical ThreadPool default, exposed for the CLI."""
    return min(32, (os.cpu_count() or 1) + 4)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("the value must be greater than zero")
    return parsed


def iter_paks(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.rglob("*.pak"))
    raise FileNotFoundError(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fast extractor for Game Republic PS3 PAK archives"
    )
    parser.add_argument("pak", type=Path, help="PAK file or directory")
    parser.add_argument("--out", type=Path, default=Path("DECOMPRESSED"))
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=default_worker_count(),
        help="decompression threads (default: %(default)s)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print one summary per archive",
    )
    args = parser.parse_args()

    try:
        pak_files = iter_paks(args.pak)
    except FileNotFoundError:
        parser.error(f"input not found: {args.pak}")

    if not pak_files:
        parser.error(f"no .pak file found in: {args.pak}")

    args.out.mkdir(parents=True, exist_ok=True)
    if len(pak_files) > 1:
        print(f"{len(pak_files)} PAK files found")

    total = 0
    failures = 0
    # Reusing one pool avoids creating two groups of threads for every PAK.
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for pak_path in pak_files:
            try:
                if len(pak_files) > 1 and not args.quiet:
                    print(f"\n=== {pak_path.name} ===")
                total += extract_pak(
                    pak_path,
                    args.out,
                    executor,
                    quiet=args.quiet,
                )
            except Exception as exc:
                failures += 1
                print(f"[ERROR] {pak_path.name}: {exc}")

    if len(pak_files) > 1:
        print(f"\nTOTAL EXTRACTED: {total} ({failures} PAK errors)")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
