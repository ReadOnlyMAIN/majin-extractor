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
from collections import Counter
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
DDM_RESOURCE_TYPE = 0x89D30498
CRG_RESOURCE_TYPE = 0x6FB6CD42


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

    # Resource records start at 0x88, with their own count and byte size.
    # The value at 0x08 is a resource type, not a record count. Scanning for
    # names and pairing them with successful inflations shifts every name
    # when the first record is skipped or a stream fails.
    entries = indexed_resources(data)
    name_counts = Counter(name for name, _, _ in entries)
    for name, resource_type, result in entries:
        relative = safe_relpath(name)
        destination = out_dir / relative
        # Several resource types deliberately share a logical name (DDM and
        # CRG for characters). Preserve both instead of overwriting the mesh.
        if resource_type == CRG_RESOURCE_TYPE:
            destination = destination.with_name(destination.name + ".crg")
        elif name_counts[name] > 1:
            if resource_type != DDM_RESOURCE_TYPE:
                destination = destination.with_name(
                    destination.name + f".resource_{resource_type:08x}"
                )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result)
        if not quiet:
            print(f"[OK] {destination.relative_to(out_dir)} ({len(result)} bytes)")
    print(f"{pak_path.name}: {len(entries)} extracted from indexed records")
    return len(entries)


def indexed_resources(data: bytes):
    """Read named resources using record sizes; validate before writing any."""
    if len(data) < 0x88 or data[:4] != PAK_MAGIC:
        raise ValueError("Invalid PAK header")
    table_size, count = struct.unpack_from(">II", data, 0x80)
    if not count or table_size != count * 0x120:
        raise ValueError("Unsupported PAK resource table")
    cursor = 0x88 + table_size
    if cursor > len(data):
        raise ValueError("Truncated PAK resource table")
    entries = []
    for index in range(count):
        record = 0x88 + index * 0x120
        size, resource_type = struct.unpack_from(">II", data, record)
        name = data[record + 16:record + 272].split(b"\0", 1)[0].decode("ascii")
        stored, _auxiliary_size, blocks, reserved = struct.unpack_from(
            ">4I", data, record + 272,
        )
        # The second size is not a duplicate uncompressed length. Most
        # resources happen to repeat `size`, but some valid XET entries (for
        # example chr100_c01/c02) store a different auxiliary value there.
        # The decoded byte count is validated against the leading `size`.
        if not safe_relpath(name).parts or reserved or not blocks:
            raise ValueError(f"Invalid PAK record {index}")
        if stored < blocks * 128:
            raise ValueError(f"Invalid stored size for PAK record {index}")
        end = cursor + stored
        if end > len(data):
            raise ValueError(f"Truncated PAK resource {name}")
        output = bytearray()
        for _ in range(blocks):
            if cursor + 128 > end:
                raise ValueError(f"Truncated block header for {name}")
            encoding, length = struct.unpack_from(">II", data, cursor)
            cursor += 128
            if cursor + length > end:
                raise ValueError(f"Truncated PAK compression block for {name}")
            payload = data[cursor:cursor + length]
            if encoding == 0:
                # Stored block. This is used by the nested language archives
                # fspe_jpn and fspe_rus; `length` is already the output size.
                output.extend(payload)
            elif encoding == 1:
                decoder = zlib.decompressobj(-15)
                output.extend(decoder.decompress(payload))
                if not decoder.eof or decoder.unused_data:
                    raise ValueError(f"Invalid deflate stream for {name}")
            else:
                raise ValueError(
                    f"Unsupported PAK block encoding {encoding} for {name}"
                )
            cursor += length
        if cursor != end or len(output) != size:
            raise ValueError(f"PAK resource size mismatch for {name}")
        entries.append((name, resource_type, bytes(output)))
    if cursor != len(data):
        raise ValueError("Unaccounted data after PAK resources")
    return entries


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
