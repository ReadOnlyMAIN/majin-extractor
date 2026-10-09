#!/usr/bin/env python3
"""Compare FK rotation spaces against a DDM bind skeleton.

This probe is intentionally geometric: it reports the model-space axis of a
joint's motion under each reconstruction model.  It does not score candidates
from anatomical plausibility.  The DDM bind transform independently supplies
the mapping from the stored joint-local axes to model axes.

Examples::

    python research/fk_rotation_probe.py chr301 --clip 2 --bone-id 110
    python research/fk_rotation_probe.py chr301 chr302 chr303 --clip 2
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.conversion.ddm.skinned import decode_skinned_skeleton  # noqa: E402
from tools.conversion.motion_decode import (  # noqa: E402
    build_canonical_anchor_map,
    decode_experimental_joint_rotations,
    decode_motion_skeleton,
    decode_root_rotation,
    decode_scalar_clip,
    derive_humanoid_scalar_starts,
    discover_character_motion,
    infer_humanoid_joint_scalar_starts,
    infer_humanoid_rotation_bases,
)


def multiply(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return (
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2,
    )


def inverse(q):
    return (-q[0], -q[1], -q[2], q[3])


def rotate(q, vector):
    value = multiply(multiply(q, (*vector, 0.0)), inverse(q))
    return value[:3]


def axis_angle(q):
    if q[3] < 0.0:
        q = tuple(-value for value in q)
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q[3])))
    sine = math.sin(angle / 2.0)
    axis = ((0.0, 0.0, 0.0) if abs(sine) < 1e-8 else
            tuple(value / sine for value in q[:3]))
    return [round(value, 6) for value in axis], round(math.degrees(angle), 6)


def inspect_character(name, clip_index, bone_id):
    model = ROOT / "game_files" / "decompressed" / "KB" / "chara" / name / name
    info = discover_character_motion(model)
    if info is None:
        raise RuntimeError(f"{name}: no motion package")
    skeleton = decode_skinned_skeleton(model.read_bytes(), info["skeleton_bone_ids"])
    motion = Path(info["sequence_path"]).read_bytes()
    boundaries = info["animation_boundaries"]
    if not 0 <= clip_index < len(boundaries) - 1:
        raise RuntimeError(f"{name}: clip {clip_index} is out of range")
    decoded = decode_scalar_clip(
        motion[boundaries[clip_index]:boundaries[clip_index + 1]],
    )
    counts = [
        decode_scalar_clip(motion[start:end])["scalar_count"]
        for start, end in zip(boundaries, boundaries[1:]) if end > start
    ]
    canonical = max(counts)
    starts, _ = infer_humanoid_joint_scalar_starts(skeleton, canonical)
    anchors = build_canonical_anchor_map(motion, boundaries, canonical, None)
    clip_starts, alignment = derive_humanoid_scalar_starts(
        decoded, starts, anchors,
    )
    reference = decode_motion_skeleton(
        motion[info["skeleton_segment_offset"]:info["first_clip_offset"]],
    )
    bases, non_rotations, _ = infer_humanoid_rotation_bases(
        motion, boundaries, skeleton, starts, canonical, anchors, reference,
    )
    by_id = {joint["global_id"]: joint["index"] for joint in skeleton["joints"]}
    joint_index = by_id[bone_id]
    if joint_index not in clip_starts:
        raise RuntimeError(f"{name}: bone {bone_id} has no verified scalar binding")

    chain = []
    current = joint_index
    while current is not None:
        chain.append(current)
        current = skeleton["joints"][current].get("parent")
    chain.reverse()
    selected = [joint for joint in chain if joint and joint in clip_starts]
    root = decode_root_rotation(decoded, skeleton, source="local")
    results = {}
    for model_name in ("local_delta_post", "local_delta_pre", "global_delta"):
        channels = decode_experimental_joint_rotations(
            decoded, skeleton, selected,
            scalar_starts=clip_starts,
            reference_skeleton=reference,
            rotation_model=model_name,
            rotation_bases=bases,
            position_triplet_joints=non_rotations,
        )
        by_joint = {channel["joint"]: channel for channel in channels}
        globals_at_endpoint = []
        for frame in (0, -1):
            global_rotation = root["values"][frame]
            for joint in selected:
                global_rotation = multiply(
                    global_rotation, by_joint[joint]["values"][frame],
                )
            globals_at_endpoint.append(global_rotation)
        results[model_name] = dict(zip(
            ("axis", "angle_degrees"),
            axis_angle(multiply(
                globals_at_endpoint[1], inverse(globals_at_endpoint[0]),
            )),
        ))

    bind_global = reference["joints"][joint_index]["rotation"]
    scalar_start = clip_starts[joint_index]
    return {
        "character": name,
        "clip_index": clip_index,
        "clip_name": info.get("clip_name_slots", [None] * (clip_index + 1))[clip_index],
        "bone_id": bone_id,
        "joint_index": joint_index,
        "source_scalar_indices": list(range(scalar_start, scalar_start + 3)),
        "per_clip_alignment": alignment,
        "bind_local_axes_in_model": {
            axis: [round(value, 6) for value in rotate(bind_global, vector)]
            for axis, vector in {
                "x": (1.0, 0.0, 0.0),
                "y": (0.0, 1.0, 0.0),
                "z": (0.0, 0.0, 1.0),
            }.items()
        },
        "models": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("characters", nargs="+", help="Character directories")
    parser.add_argument("--clip", type=int, default=2, help="Physical clip index")
    parser.add_argument("--bone-id", type=int, default=110, help="Bone ID to inspect")
    args = parser.parse_args()
    print(json.dumps([
        inspect_character(name, args.clip, args.bone_id)
        for name in args.characters
    ], indent=2))


if __name__ == "__main__":
    main()
