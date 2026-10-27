#!/usr/bin/env python3
"""Exercise compact TRS sampling and declared IK on independent rig families.

By default, bake the first nonempty curve of every package and discard channel
arrays after collecting diagnostics. --all-blocks exercises the whole corpus.
An exact endpoint solve does not prove HexaEngine's pole or helper constraints.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.pose import decode_transform_clip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assets', nargs='*')
    parser.add_argument('--all-blocks', action='store_true')
    parser.add_argument('--kb-root', type=Path, default=Path(__file__).resolve().parents[1]
                        / 'game_files/decompressed/KB')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    totals = Counter()
    packages = []
    maximum = 0.0
    for path in sorted((args.kb_root / 'motionPackage').glob('*/BigEndian/*')):
        if args.assets and path.name not in args.assets:
            continue
        package = parse_motion_package(path)
        entry = dict(asset=path.name, blocks=[], rejected=[])
        packages.append(entry)
        totals['packages'] += 1
        blocks = package.curve_blocks if args.all_blocks else package.curve_blocks[:1]
        for block in blocks:
            try:
                clip = decode_transform_clip(package, block)
            except (RuntimeError, ValueError) as exc:
                entry['rejected'].append(dict(block=block.block_index, error=str(exc)))
                totals['rejected_blocks'] += 1
                continue
            totals['baked_blocks'] += 1
            totals['solved_ik_samples'] += clip['ik']['solved_samples']
            totals['clamped_ik_samples'] += clip['ik']['clamped_samples']
            maximum = max(maximum, clip['ik']['max_reachable_error'])
            entry['blocks'].append(dict(block=block.block_index, samples=clip['sample_count'],
                                       channels=len(clip['channels']), ik=clip['ik']))
        print(path.name, len(entry['blocks']), 'baked;', len(entry['rejected']), 'rejected', flush=True)
    result = dict(totals=dict(totals), max_reachable_error=maximum, packages=packages)
    print(json.dumps({key: value for key, value in result.items() if key != 'packages'}, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
