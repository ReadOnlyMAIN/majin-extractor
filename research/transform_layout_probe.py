#!/usr/bin/env python3
"""Reproduce the compact TRS grammar and independently test rotation orders.

Run from any directory: python research/transform_layout_probe.py [asset ...]
The default scans all packages; --json writes the complete machine-readable
evidence, including rejected blocks and discriminating DDM bind anchors.
"""
from __future__ import annotations

import argparse
from collections import Counter
import itertools
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.conversion.ddm.skinned import decode_skinned_skeleton
from tools.conversion.motion.curves import decode_curve_block
from tools.conversion.motion.layout import decode_transform_layout
from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.sequence import (
    duration_frames_of, parse_motion_sequence, parse_sequence_index,
)
from tools.conversion.motion.ik import discover_ik_chains
from tools.conversion.motion.transforms import (
    euler_quaternion, local_reference_rotations, sample_scalar,
)


def angular_error(a, b):
    cosine = abs(sum(x*y for x, y in zip(a, b)))
    cosine /= math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    return 2*math.acos(min(1.0, cosine))


def inspect(root, names):
    totals = Counter()
    packages = []
    anchors = {}
    orders = list(map(''.join, itertools.permutations('xyz')))
    paths = sorted((root / 'motionPackage').glob('*/BigEndian/*'))
    for path in paths:
        if names and path.name not in names:
            continue
        package = parse_motion_package(path)
        skeleton = package.skeleton
        item = dict(asset=path.name, bones=skeleton.bone_count,
                    chains=skeleton.chain_count, exact_blocks=0, rejected=[])
        packages.append(item)
        totals['packages'] += 1
        if len(discover_ik_chains(skeleton)) != skeleton.chain_count:
            raise AssertionError((path.name, 'IK chain count'))
        totals['ik_graphs_exact'] += 1
        sequence_path = root / 'motionSequence' / path.name / path.name
        sequence = records = None
        if sequence_path.is_file():
            sequence = parse_sequence_index(sequence_path)
            _, records = parse_motion_sequence(sequence_path)
            totals['sequence_tables_exact'] += 1
        item['state_duration_mismatches'] = []
        try:
            ddm = decode_skinned_skeleton(
                (root / 'chara' / path.name / path.name).read_bytes())
            if ddm['transform_bone_ids'][:skeleton.bone_count] != skeleton.bone_ids:
                ddm = None
        except (OSError, RuntimeError):
            ddm = None
        if ddm is not None:
            local = local_reference_rotations(skeleton)
            for i, joint in enumerate(ddm['joints'][:skeleton.bone_count]):
                if joint['parent'] != skeleton.parent_indices[i]:
                    raise AssertionError((path.name, i, 'parent mismatch'))
                if angular_error(local[i], joint['rotation']) > 1e-4:
                    totals['reference_rotation_differences'] += 1
                else:
                    totals['reference_rotation_matches'] += 1
        for block in package.curve_blocks:
            try:
                decoded = decode_curve_block(block)
                layout = decode_transform_layout(decoded, skeleton.bone_count)
            except (ValueError, RuntimeError) as exc:
                item['rejected'].append(dict(block=block.block_index, reason=str(exc)))
                totals['rejected_blocks'] += 1
                continue
            totals['exact_blocks'] += 1
            item['exact_blocks'] += 1
            if sequence is not None:
                for record_index in sequence.records_for_slot(block.slot_index):
                    record = records[record_index]
                    if duration_frames_of(record) == decoded.sample_count - 1:
                        totals['indexed_state_duration_matches'] += 1
                    else:
                        totals['indexed_state_duration_mismatches'] += 1
                        item['state_duration_mismatches'].append(dict(
                            slot=block.slot_index, record=record.short_name,
                            state_duration=duration_frames_of(record),
                            segment_duration=decoded.sample_count-1))
            for group in layout.groups:
                if group.implicit:
                    continue
                if group.path == 'scale':
                    totals['explicit_scale_groups'] += 1
                if group.path == 'translation':
                    for axis, track in enumerate(group.tracks):
                        ref = skeleton.translations[group.joint][axis]
                        if abs(ref) > 1e-3 and abs(sample_scalar(track, 0)-ref) < 1e-3:
                            totals['nonzero_translation_anchor_components'] += 1
                if ddm is None or group.path != 'rotation' or any(
                        track.mode in (6, 7) for track in group.tracks):
                    continue
                angles = tuple(sample_scalar(t, 0) for t in group.tracks)
                target = ddm['joints'][group.joint]['rotation']
                errors = {
                    f'{order}_{unit}': angular_error(
                        euler_quaternion([v*factor for v in angles], order), target)
                    for order in orders for unit, factor in (
                        ('radians', 1), ('degrees', math.pi/180))
                }
                others = [v for k, v in errors.items() if k != 'xyz_radians']
                if errors['xyz_radians'] < 1e-4 and min(others) > 0.01:
                    # Deduplicate repeated bind constants across clips and assets.
                    key = tuple(round(v, 5) for v in (*angles, *target))
                    anchors.setdefault(key, dict(
                        asset=path.name, block=block.block_index, joint=group.joint,
                        bone_id=skeleton.bone_ids[group.joint],
                        scalar_start=group.scalar_start, angles=angles,
                        ddm_local_quaternion=target, errors_radians=errors))
    return dict(totals=dict(totals), packages=packages,
                discriminating_rotation_anchors=list(anchors.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assets', nargs='*')
    parser.add_argument('--kb-root', type=Path, default=Path(__file__).resolve().parents[1]
                        / 'game_files/decompressed/KB')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    result = inspect(args.kb_root, set(args.assets))
    print(json.dumps(result['totals'], indent=2))
    anchors = result['discriminating_rotation_anchors']
    print(f'Unique noncommuting DDM anchors selecting XYZ radians: {len(anchors)}')
    for item in anchors[:5]:
        print(item['asset'], 'bone', item['bone_id'], item['angles'],
              'error', item['errors_radians']['xyz_radians'])
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
