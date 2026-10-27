#!/usr/bin/env python3
"""Inspect local player/cinematic model selection evidence for the examined PS3 build.

Reads source MHCP tables and a decrypted local ELF. No game payload is emitted.
This records bounded, verified paths; it does not infer every actor's runtime state.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.research.tar_cpu_inspect import ElfMemory


def mhcp_chunks(data: bytes, signature: bytes) -> list[bytes]:
    """Read the allocation directory relative to the container at file offset 0x80."""
    if len(data) < 0xa0 or data[0x80:0x84] != signature:
        raise ValueError('unexpected table container signature')
    count, directory = struct.unpack_from('>II', data, 0x88)
    if directory != 0x20 or count > (len(data) - 0xa0) // 8:
        raise ValueError('invalid table allocation directory')
    end = 0xa0 + count * 8
    result = []
    for i in range(count):
        relative, size = struct.unpack_from('>II', data, 0xa0 + i * 8)
        offset = 0x80 + relative
        if offset < end or offset + size > len(data):
            raise ValueError('table allocation outside payload')
        result.append(data[offset:offset + size])
    return result


def cstring(memory: ElfMemory, address: int) -> str:
    result = bytearray()
    for i in range(256):
        byte = memory.read(address + i, 1)
        if byte == b'\0':
            return result.decode('ascii')
        result += byte
    raise ValueError('unterminated resource string')


def cinematic_costumes(memory: ElfMemory, toc: int) -> list[dict]:
    """Recover the actual switch destinations, including the reordered last three."""
    if memory.word(0x15880) != 0x2b80000a:
        raise ValueError('different build: costume switch must have 11 cases')
    rows = []
    for item in range(11):
        relative = struct.unpack('>i', memory.read(0x158a4 + 4 * item))[0]
        pc = 0x158a4 + relative
        instruction = memory.word(pc)
        if instruction >> 26 != 32 or (instruction >> 16) & 31 != 2:
            raise ValueError('unexpected costume path load')
        displacement = struct.unpack('>h', struct.pack('>H', instruction & 65535))[0]
        path = cstring(memory, memory.word(toc + displacement))
        if not re.fullmatch(r'KB/chara/(chr1\d\d)/\1', path):
            raise ValueError('unexpected cinematic player path')
        rows.append(dict(item_id=item, path=path, ppu_address=hex(pc)))
    return rows


def inspect(kb_root: Path, elf: bytes) -> dict:
    table_root = kb_root / 'data' / 'data_base'
    paths = [chunk.rstrip(b'\0').decode('ascii') for chunk in
             mhcp_chunks((table_root / 'PathCharaModel').read_bytes(), b'MHCP')]
    if len(paths) != 216:
        raise ValueError('expected three columns of 72 model paths')
    item_chunks = mhcp_chunks((table_root / 'LinkItemData').read_bytes(), b'MTIL')
    if len(item_chunks) != 1 or len(item_chunks[0]) % 52:
        raise ValueError('expected 52-byte LinkItemData records')
    items = [struct.unpack_from('>13I', item_chunks[0], offset)
             for offset in range(0, len(item_chunks[0]), 52)]
    equip_paths = [chunk.rstrip(b'\0').decode('ascii') for chunk in
                   mhcp_chunks((table_root / 'PathCharaEquip').read_bytes(), b'EHCP')]
    memory = ElfMemory(elf)
    toc = memory.word(memory.entry + 4)
    cinema = cinematic_costumes(memory, toc)
    costumes = []
    for index, cinematic in enumerate(cinema):
        row = items[index]
        if row[0] != 2 or row[5] != 0 or row[1] >= 72:
            raise ValueError('expected body costume item records')
        costumes.append(dict(item_id=index, gameplay_model_index=row[1],
                             gameplay_path=paths[row[1]], cinematic_path=cinematic['path'],
                             cinematic_switch_pc=cinematic['ppu_address'],
                             shared_column_1=paths[72 + row[1]],
                             shared_column_2=paths[144 + row[1]]))
    demo_counts = Counter()
    demo_files = []
    for path in sorted((kb_root / 'demo').rglob('*')):
        if not path.is_file():
            continue
        refs = sorted({m.decode() for m in re.findall(rb'KB/chara/(?:chr1\d\d)/(?:chr1\d\d)', path.read_bytes())})
        if refs:
            demo_counts.update(refs)
            demo_files.append(dict(file=str(path.relative_to(kb_root)), models=refs))
    equipment = []
    for index, row in enumerate(items):
        if row[5] not in (1, 2):
            continue
        equipment.append(dict(item_id=index, category_value=row[5],
                              model_index=row[1], equip_index=row[2],
                              equip_table_entry=equip_paths[row[2]] if row[2] < 58 else None))
    # Validate the key routing instructions before attributing their semantics.
    for pc, instruction in {0x44ae98: 0x4bbca9d5, 0x41ed14: 0x4bc18b45,
                            0x145e24: 0x8122bae8, 0x53a620: 0x1fbd0034,
                            0x53aa7c: 0x4bfdd415}.items():
        if memory.word(pc) != instruction:
            raise ValueError(f'different build at {pc:#x}')
    return dict(elf_sha256=hashlib.sha256(elf).hexdigest(), costumes=costumes,
                cinematic_player_family=[cstring(memory, memory.word(0xe01644 + i * 4)) for i in range(11)],
                demo_reference_counts=dict(demo_counts), demo_files=demo_files,
                equipment_records=equipment,
                debug_slot_labels=['_currentItemBody', '_currentItemHead', '_currentItemBracelet'],
                evidence=dict(cinematic_path_selector='0x1586c', cinematic_model_id_selector='0x37858',
                              family_recognizer='0x145e24', resource_substitution='0x44ae6c..0x44aea0',
                              model_substitution='0x41ecec..0x41ed28',
                              gameplay_model_lookup='0x53aa58', item_record_lookup='0x53a604'),
                limitations=['cinematic usage is established; other clean-family uses are not excluded',
                             'no live RSX state capture: depth bias and draw ordering remain unverified',
                             'the complete accessory attachment path is not reconstructed here',
                             'no HP-controlled whole-model swap was found in these selection paths'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kb-root', required=True, type=Path)
    parser.add_argument('--elf', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect(args.kb_root, args.elf.read_bytes())
    result = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end='')


if __name__ == '__main__':
    main()
