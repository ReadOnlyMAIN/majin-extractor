"""Trace numeric player motion selection in the examined local PS3 executable.

This research probe does not rename animations or modify an export. Expected
names supplied by the user are search queries, never definitions or overrides.
Run with python -m research.player_motion_selection_probe --elf PATH --json PATH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from tools.conversion.motion.curves import decode_curve_block
from tools.conversion.motion.layout import decode_transform_layout
from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.transforms import sample_scalar
from tools.research.tar_cpu_inspect import ElfMemory


# Instruction evidence: table lookup -> engine trampoline -> raw offset lookup.
# These guards reject unrelated executable builds, rather than reusing addresses.
INSTRUCTIONS = {
    0x2C94A4: 0x80C2E8A4,  # pointer-pair array from application TOC
    0x2C94D4: 0x7C06482E,  # load first pointer in selected pair
    0x2C94E0: 0x9003199C,  # save first table on character
    0x2C94F4: 0x901D19A0,  # save second table on character
    0x2B6CE4: 0x392900C4,  # alternate first-table pointer: +196 bytes
    0x2D2F50: 0x2B840062,  # first lookup branch: logical id <=98
    0x2D2F7C: 0x8123199C,
    0x2D2F90: 0x3929FFFE,  # first table: 2*(logical id-1)
    0x2D2FA0: 0xA3C90000,  # u16 motion number
    0x2D2FE8: 0x4892A789,  # call engine trampoline
    0x2D303C: 0x812319A0,
    0x2D3050: 0x3929FF38,  # second table: 2*(logical id-100)
    0x2D3060: 0xA3C90000,
    0xBFD77C: 0x4BB1CD14,  # trampoline -> 0x71a490
    0x719954: 0x80040004,  # initialize skeleton from first offset
    0x719964: 0x91690038,  # retain raw table base
    0x71A4A8: 0x5489103A,  # motion number *4
    0x71A4B8: 0x808B0038,  # raw table base
    0x71A4C8: 0x80090000,  # load offset indexed by motion number
    0x71A4CC: 0x7C840214,  # base + offset
}


def fnv1_name(text: str) -> int:
    """The engine's multiply-then-xor hash, not evidence it hashes clip names."""
    value = 0x811C9DC5
    for byte in text.encode('ascii'):
        value = ((value * 0x01000193) & 0xFFFFFFFF) ^ byte
    return value


def archive_references(root: Path) -> dict:
    archives = records = 0
    matches = []
    for path in sorted(root.rglob('*.pak')):
        with path.open('rb') as stream:
            header = stream.read(0x88)
            if len(header) != 0x88 or header[:4] != b'\0kap':
                continue
            size, count = struct.unpack_from('>II', header, 0x80)
            if not count or size != count * 0x120 or size > path.stat().st_size - 0x88:
                continue
            table = stream.read(size)
        archives += 1
        records += count
        for index in range(count):
            record = table[index * 0x120:(index + 1) * 0x120]
            name = record[16:272].split(b'\0', 1)[0].decode('ascii')
            if name in ('KB/motionSequence/chr100/chr100',
                        'KB/motionPackage/chr100/BigEndian/chr100'):
                length, resource_type = struct.unpack_from('>II', record)
                matches.append(dict(archive=str(path), record=index, name=name,
                                    resource_type=hex(resource_type), decoded_size=length))
    return dict(archives=archives, resource_records=records, matches=matches)


def inspect(elf_path: Path, kb_root: Path) -> dict:
    executable = elf_path.read_bytes()
    memory = ElfMemory(executable)
    for pc, expected in INSTRUCTIONS.items():
        if memory.word(pc) != expected:
            raise ValueError(f'unsupported executable: instruction differs at {pc:#x}')
    toc = memory.word(memory.entry + 4)
    pairs = memory.word(toc - 5980)
    first, second = struct.unpack('>II', memory.read(pairs, 8))
    source = kb_root / 'motionPackage/chr100/BigEndian/chr100'
    sequence = kb_root / 'motionSequence/chr100/chr100'
    raw = source.read_bytes()
    package = parse_motion_package(source)
    by_offset = {block.offset: block for block in package.curve_blocks}
    # Read explicit cells associated with the user-reported segments.
    # This does not infer a whole-table length or the active runtime table.
    tables = [
        ('first_default', first, 1, [3, 4, 5, 6, 58, 59, 78, 79, 82, 83]),
        ('first_shifted', first + 196, 1, [58, 59]),
        ('second_default', second, 100,
         [154, 155, 156, 157, 158, 159, 160, 161, 175, 176,
          270, 271, 285, 286, 287, 288, 289, 308]),
    ]
    selections = []
    for table, pointer, start, ids in tables:
        for logical_id in ids:
            address = pointer + 2 * (logical_id - start)
            number = struct.unpack('>H', memory.read(address, 2))[0]
            if number <= 1:
                raise ValueError('selected motion number has no curve payload')
            # Apply the actual engine word lookup to the source package.
            # Word 0 is the count, word 1 the skeleton offset. Curve slot
            # numbers are therefore distinct from engine motion numbers.
            relative = struct.unpack_from('>I', raw, 0x80 + 4 * number)[0]
            block = by_offset.get(0x80 + relative)
            if block is None or block.slot_index != number - 1:
                raise ValueError('selected cell does not address its own physical curve block')
            decoded = decode_curve_block(block)
            layout = decode_transform_layout(decoded, package.skeleton.bone_count)
            control = layout.controls[0]
            root_rotation = next(group for group in layout.groups
                                 if group.joint == 0 and group.path == 'rotation')
            selections.append(dict(
                table=table, cell_address=hex(address), logical_id=logical_id,
                engine_motion_number=number, package_slot=block.slot_index,
                source_slot=block.slot_index - 1, payload_offset=hex(block.offset),
                duration_frames=decoded.sample_count - 1,
                first_control_endpoints=[sample_scalar(control, 0),
                                        sample_scalar(control, decoded.sample_count - 1)],
                root_rotation_endpoints=(None if root_rotation.implicit else [
                    [sample_scalar(track, frame) for track in root_rotation.tracks]
                    for frame in (0, decoded.sample_count - 1)])))
    queries = []
    search_data = [('executable', executable), ('sequence', sequence.read_bytes()),
                   ('package', raw)]
    for name in ('c100_0050_cro_turn_90_l_00', 'c100_0050_cro_turn_90_r_00'):
        hashes = []
        for prefix in ('', 'KB/motionSequence/chr100/'):
            value = fnv1_name(prefix + name)
            hashes.append(dict(input=prefix + name, fnv1=hex(value),
                byte_matches={label: {order: data.find(value.to_bytes(4, order))
                                     for order in ('big', 'little')}
                              for label, data in search_data}))
        queries.append(dict(expected_string=name, query_source='user',
                            literal_matches={label: any(name.encode(encoding) in data
                                for encoding in ('ascii', 'utf-16-le', 'utf-16-be'))
                                for label, data in search_data}, hash_queries=hashes))
    return dict(executable_sha256=hashlib.sha256(executable).hexdigest(),
                package_sha256=hashlib.sha256(raw).hexdigest(),
                instruction_guards={hex(pc): hex(word) for pc, word in INSTRUCTIONS.items()},
                pointer_pair_array=hex(pairs), selections=selections, name_queries=queries,
                hash_calibration=dict(input='resMotionSequencePackage',
                                      computed=hex(fnv1_name('resMotionSequencePackage')),
                                      pak_resource_type='0x108dc1e8'),
                limitations=[
                    'numeric selection is not recovery of an original name string',
                    'active table pair/posture depends on runtime state',
                    'the first control endpoints are measured; full engine control semantics remain open',
                    'no animation names or exported assets are modified'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--elf', required=True, type=Path)
    parser.add_argument('--kb-root', type=Path, default=Path('game_files/decompressed/KB'))
    parser.add_argument('--json', type=Path)
    parser.add_argument('--pak-root', type=Path)
    args = parser.parse_args()
    report = inspect(args.elf, args.kb_root)
    if args.pak_root:
        report['archive_lookup'] = archive_references(args.pak_root)
    output = json.dumps(report, indent=2) + '\n'
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
