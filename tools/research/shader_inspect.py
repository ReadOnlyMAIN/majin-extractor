#!/usr/bin/env python3
"""Inspect parameters and fragment programs in PS3 ``0bxf/fxbf`` shaders."""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path


OUTER_MAGIC = b"0bxf"
CONTAINER_MAGIC = b"fxbf"
CONTAINER_OFFSET = 0x80
PROGRAM_TABLE_OFFSET = 0x18
PROGRAM_ENTRY_SIZE = 20
PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _be_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _be_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _cstring(data: bytes, offset: int) -> str:
    if not 0 <= offset < len(data):
        raise ValueError(f"String offset outside shader: 0x{offset:x}")
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"Unterminated shader string at 0x{offset:x}")
    return data[offset:end].lstrip(b"#").decode("ascii")


def _constant_relocations(
    data: bytes, base: int, table_offset: int, program_size: int,
) -> list[int]:
    """Decode Cg fragment-constant patch locations.

    Each four-byte entry contains a big-endian uint16 byte offset followed by
    a zero uint16. Unused table space is filled with ``####``.
    """
    if not table_offset:
        return []
    cursor = base + table_offset
    relocations = []
    while cursor + 4 <= len(data) and data[cursor:cursor + 4] != b"####":
        program_offset, reserved = struct.unpack_from(">HH", data, cursor)
        if reserved or program_offset % 16 or program_offset >= program_size:
            break
        relocations.append(program_offset)
        cursor += 4
    return relocations


def inspect_shader(data: bytes) -> dict:
    if data[:4] != OUTER_MAGIC:
        raise ValueError("Not a 0bxf shader resource")
    base = data.find(CONTAINER_MAGIC, CONTAINER_OFFSET)
    if base < 0:
        raise ValueError("Missing fxbf shader container")

    program_count = _be_u16(data, base + 6)
    fragment_pool = base + _be_u32(data, base + 12)
    headers = []
    starts = []
    ends = []
    sampler_tables = []
    fragment_ranges = []
    for index in range(program_count):
        entry = base + PROGRAM_TABLE_OFFSET + index * PROGRAM_ENTRY_SIZE
        name_relative = _be_u32(data, entry)
        descriptor_relative = _be_u32(data, entry + 4)
        descriptor = base + descriptor_relative
        headers.append((name_relative, descriptor_relative))
        starts.append(base + _be_u32(data, descriptor + 4))
        ends.append(base + _be_u32(data, descriptor + 16))
        sampler_tables.append((
            base + _be_u32(data, descriptor + 20) + 8,
            _be_u32(data, descriptor + 24),
        ))
        fragment_start = fragment_pool + _be_u32(data, descriptor + 36)
        fragment_size = _be_u32(data, descriptor + 40)
        fragment_ranges.append((
            fragment_start,
            fragment_start + fragment_size,
        ))

    programs = []
    for (
        (name_relative, descriptor_relative), start, end,
        (sampler_start, sampler_count), fragment_range,
    ) in zip(
        headers, starts, ends, sampler_tables, fragment_ranges
    ):
        parameters = []
        seen = set()
        candidate_offsets = list(range(start, max(start, end - 15), 4))
        candidate_offsets.extend(
            sampler_start + index * 16 for index in range(sampler_count)
        )
        for offset in candidate_offsets:
            if offset < 0 or offset + 16 > len(data):
                continue
            name_offset, metadata_word, value_index, value_offset = struct.unpack_from(
                ">IIII", data, offset,
            )
            if name_offset == 0:
                continue
            absolute_name = base + name_offset
            if not base <= absolute_name < len(data):
                continue
            try:
                parameter_name = _cstring(data, absolute_name)
            except (UnicodeDecodeError, ValueError):
                continue
            if len(parameter_name) < 3 or not PARAMETER_NAME_RE.fullmatch(parameter_name):
                continue
            binding = (parameter_name, metadata_word, value_index, value_offset)
            if binding not in seen:
                seen.add(binding)
                item = {
                    "name": parameter_name,
                    "metadata_word": f"0x{metadata_word:08x}",
                    "resource_index": (metadata_word >> 22) & 0x1F,
                    "value_index": value_index,
                    "value_offset": value_offset,
                }
                if parameter_name.startswith("textureSampler"):
                    item["kind"] = "sampler"
                    item["texture_unit"] = item["resource_index"]
                else:
                    item["kind"] = "uniform"
                    item["fragment_constant_offsets"] = _constant_relocations(
                        data,
                        base,
                        value_offset,
                        fragment_range[1] - fragment_range[0],
                    )
                parameters.append(item)

        samplers = [
            {"name": item["name"], "texture_unit": item["texture_unit"]}
            for item in parameters if item["kind"] == "sampler"
        ]

        programs.append({
            "name": _cstring(data, base + name_relative),
            "descriptor_offset": descriptor_relative,
            "parameter_table_offset": start - base,
            "fragment_program_offset": fragment_range[0] - base,
            "fragment_program_size": fragment_range[1] - fragment_range[0],
            "parameters": parameters,
            "samplers": samplers,
        })

    return {"program_count": program_count, "programs": programs}


def iter_shader_paths(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from sorted(item for item in path.rglob("*") if item.is_file())


def extract_program_blobs(data: bytes) -> list[tuple[str, bytes]]:
    """Extract fragment NV40 programs in RSX upload byte order."""
    base = data.find(CONTAINER_MAGIC, CONTAINER_OFFSET)
    if data[:4] != OUTER_MAGIC or base < 0:
        raise ValueError("Not an fxbf shader resource")
    program_count = _be_u16(data, base + 6)
    fragment_pool = base + _be_u32(data, base + 12)
    items = []
    for index in range(program_count):
        entry = base + PROGRAM_TABLE_OFFSET + index * PROGRAM_ENTRY_SIZE
        name = _cstring(data, base + _be_u32(data, entry))
        descriptor = base + _be_u32(data, entry + 4)
        start = fragment_pool + _be_u32(data, descriptor + 36)
        end = start + _be_u32(data, descriptor + 40)
        if not base <= start < end <= len(data):
            raise ValueError(f"Invalid fragment-program range for {name}")
        payload = data[start:end]
        if len(payload) % 16:
            raise ValueError(f"Unaligned fragment program for {name}")
        items.append((name, payload))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="0bxf shader file or directory")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--dump-programs", type=Path,
        help="write each raw NV40 program as a .fp file for a decompiler",
    )
    args = parser.parse_args()

    reports = []
    for path in iter_shader_paths(args.input):
        try:
            report = inspect_shader(path.read_bytes())
        except (OSError, ValueError, struct.error):
            continue
        report["file"] = str(path)
        reports.append(report)
        if args.dump_programs:
            args.dump_programs.mkdir(parents=True, exist_ok=True)
            for name, payload in extract_program_blobs(path.read_bytes()):
                (args.dump_programs / f"{name}.fp").write_bytes(payload)

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            print(f'[{report["file"]}]')
            for program in report["programs"]:
                bindings = ", ".join(
                    f'{item["name"]}={item["texture_unit"]}'
                    for item in program["samplers"]
                )
                print(f'  {program["name"]}: {bindings or "no samplers"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
