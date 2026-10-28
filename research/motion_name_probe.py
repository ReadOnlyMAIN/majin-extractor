"""Audit current physical-slot bindings and unresolved motion names.

Run: python -m research.motion_name_probe --asset chr100 --json output/research/chr100-names.json
Optional --elf scans a local decrypted executable for literal character names.
No poses or names are inferred from neighboring slots or duration alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.sequence import duration_frames_of, parse_motion_sequence, parse_sequence_index


def inspect(kb_root: Path, asset: str) -> dict:
    sequence = kb_root / 'motionSequence' / asset / asset
    source = kb_root / 'motionPackage' / asset / 'BigEndian' / asset
    package = parse_motion_package(source)
    _, records = parse_motion_sequence(sequence)
    index = parse_sequence_index(sequence)
    curves = {block.slot_index: block for block in package.curve_blocks}
    bindings, unresolved = [], []
    for table_number, table in enumerate(index.tables):
        for slot, record_index in enumerate(table):
            if record_index == 0xFFFFFFFF:
                continue
            record = records[record_index]
            block = curves.get(slot)
            frames = int.from_bytes(block.header[2:], 'big') - 1 if block else None
            row = dict(name=record.short_name, record=record_index,
                       sequence_table=table_number, sequence_package_slot=slot,
                       state_frames=duration_frames_of(record), curve_frames=frames)
            if block:
                row.update(source_slot=block.slot_index - 1, offset=block.offset,
                           binding='physical_slot_and_duration')
            (bindings if block and row['state_frames'] == frames else unresolved).append(row)
    named_offsets = {row['offset'] for row in bindings}
    unnamed = [dict(name=f'segment_{block.slot_index - 1:03d}',
                    source_slot=block.slot_index - 1, offset=block.offset,
                    frames=int.from_bytes(block.header[2:], 'big') - 1,
                    package_slot=block.slot_index)
               for block in package.curve_blocks if block.offset not in named_offsets]
    return dict(asset=asset, source_sequence=str(sequence), source_package=str(source),
                sequence_sha256=hashlib.sha256(sequence.read_bytes()).hexdigest(),
                package_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                named_records=len(records), named_bindings=bindings,
                rejected_hypothesis='equal offsets and durations do not prove name aliases',
                unnamed_segments=unnamed, unresolved_states=unresolved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kb-root', type=Path, default=Path('game_files/decompressed/KB'))
    parser.add_argument('--asset', default='chr100')
    parser.add_argument('--elf', type=Path)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    report = inspect(args.kb_root, args.asset)
    if args.elf:
        data = args.elf.read_bytes()
        prefix = args.asset.replace('chr', 'c', 1) + '_'
        report['executable_name_literals'] = sorted({match.decode('ascii') for match in
            re.findall(re.escape(prefix.encode('ascii')) + rb'[A-Za-z0-9_]+', data)})
        report['executable_sha256'] = hashlib.sha256(data).hexdigest()
    output = json.dumps(report, indent=2) + '\n'
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
