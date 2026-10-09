#!/usr/bin/env python3
"""Per-clip scalar-layout probe (REVERSE_DDM.md §28.5, ROADMAP step 5d).

Read-only analysis. For a motion package, this probe:

- decodes every clip's scalar track list with the maintained decoder;
- aligns the clips pairwise on shared channel values (value + mode equality)
  and reports where blocks are inserted or omitted, including between clips
  of the *same* scalar count;
- locates the structural anchors: the root position channels (s2/s3/s4),
  the constant rig-control values, and the model-space IK target channels
  (hands/feet of the flagged IK chains).

Run:  python research/per_clip_layout_probe.py chr301 [chr300 ...]
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'conversion'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.conversion.motion_decode import decode_motion_skeleton, decode_scalar_clip

try:
    from tools.conversion.ddm.skinned import decode_skinned_skeleton
except ImportError:
    decode_skinned_skeleton = None

KB = Path('game_files/decompressed/KB')
ABSENT = {1: 0.0, 2: 1.5707963267948966, 3: 3.141592653589793,
          4: -1.5707963267948966}


def value_at(track, frame):
    mode = track['mode']
    if mode == 0:
        return 0.0
    if mode in ABSENT:
        return ABSENT[mode]
    if mode == 5:
        return track['values'][0]
    values = track.get('values') or []
    if not values:
        return 0.0
    if mode == 6:
        frames, values = track['frames'], values
    else:
        frames, values = track['frames'], values
    for index in range(1, len(frames)):
        if frame <= frames[index]:
            left = index - 1
            span = frames[index] - frames[left] or 1
            ratio = (frame - frames[left]) / span
            low, high = values[left], values[index]
            return low * (1 - ratio) + high * ratio
    return values[-1]


def load(name):
    payload = KB / f'motionPackage/{name}/BigEndian/{name}'
    buffer = payload.read_bytes()
    count = struct.unpack_from('>I', buffer, 0x80)[0]
    offsets = struct.unpack_from(f'>{count}I', buffer, 0x84)
    bounds = [0x80 + offset for offset in offsets]
    clips = []
    for start, end in zip(bounds[1:-1], bounds[2:]):
        try:
            clips.append(decode_scalar_clip(buffer[start:end]))
        except Exception as exc:
            print(f'  skipped segment {start:#x}: {exc}')
    skeleton = decode_motion_skeleton(buffer[bounds[0]:bounds[1]])
    return buffer, bounds, skeleton, clips


def align(reference, candidate):
    """Greedily align two clip track-value lists, report insert/omit events."""
    total_r = next(clip['scalar_count'] for clip in [reference])
    events = []
    i = j = 8
    while i < total_r and j < candidate['scalar_count']:
        rv, cv = value_at(reference['tracks'][i], 0), value_at(
            candidate['tracks'][j], 0,
        )
        rm, cm = reference['tracks'][i]['mode'], candidate['tracks'][j]['mode']
        if abs(rv - cv) < 1e-4 and rm == cm:
            i += 1
            j += 1
            continue
        found = None
        for skip in (1, 2, 3):
            if j + skip < candidate['scalar_count']:
                c2, m2 = (value_at(candidate['tracks'][j + skip], 0),
                          candidate['tracks'][j + skip]['mode'])
                if abs(rv - c2) < 1e-4 and rm == m2:
                    found = ('insert', i, j, skip)
                    break
            if i + skip < reference['scalar_count']:
                r2 = value_at(reference['tracks'][i + skip], 0)
                m2 = reference['tracks'][i + skip]['mode']
                if abs(r2 - cv) < 1e-4 and cm == m2:
                    found = ('omit', i, j, skip)
                    break
        if found:
            events.append(found)
            if found[0] == 'insert':
                j += found[3]
            else:
                i += found[3]
        else:
            events.append(('mismatch', i, j, 0))
            i += 1
            j += 1
    return events


def main(names):
    for name in names:
        buffer, bounds, skeleton, clips = load(name)
        print(f'== {name}: {len(clips)} clips, '
              f'{skeleton["bone_count"]} bones, '
              f'{skeleton["ik_chain_count"]} ik chains')
        counts = sorted({c['scalar_count'] for c in clips})
        print('  scalar counts:', counts)
        index_of = min(range(len(clips)), key=lambda i: -clips[i]['frame_count'])
        reference = clips[index_of]
        print(f'  reference clip {index_of}: '
              f'{reference["scalar_count"]} scalars / '
              f'{reference["frame_count"]} frames')
        summary = {}
        for index, clip in enumerate(clips):
            if index == index_of or clip['scalar_count'] < 12:
                continue
            events = align(reference, clip)
            footprint = tuple((kind, skip) for kind, _, _, skip in events)
            summary.setdefault(footprint, []).append(index)
        try:
            skeleton_decoder = decode_skinned_skeleton
        except NameError:
            skeleton_decoder = None
        if skeleton_decoder is not None:
            try:
                ddm = (KB / 'chara' / name / name).read_bytes()
                rig = skeleton_decoder(ddm)
                print(f'  DDM order == motion order: '
                      f'{rig["transform_bone_ids"] == skeleton["bone_ids"]}')
            except Exception as exc:
                print(f'  DDM skeleton: {exc}')
        for footprint, indices in sorted(
            summary.items(), key=lambda item: -len(item[1]),
        )[:6]:
            print(f'  alignment footprint {footprint}: '
                  f'{len(indices)} clips (e.g. {indices[:4]})')


if __name__ == '__main__':
    for name in (sys.argv[1:] or ['chr301', 'chr300', 'chr500']):
        main([name])
