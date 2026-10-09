#!/usr/bin/env python3
"""Probe structural discriminants for rotation-vs-position and FK-vs-IK.

The goal is a *generic* binding rule: the engine already flags IK chains in the
motion reference skeleton, so FK/IK should come from ``constraint_flags`` rather
than from hard-coded bone IDs. This tool cross-checks the flags against the
scalar data (magnitudes, invariance, keyframe presence) to test whether
rotation-vs-position can also be decided structurally.

Run::

    python research/motion_semantics_probe.py chr300 chr301 chr500 chr560
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "game_files/decompressed/KB"
sys.path.insert(0, str(ROOT / "tools" / "conversion"))

from motion_decode import (  # noqa: E402
    decode_motion_skeleton,
    decode_scalar_clip,
    discover_character_motion,
)


def load(name):
    info = discover_character_motion(KB / "chara" / name / name)
    motion = Path(info["sequence_path"]).read_bytes()
    return info, motion


def flag_table(info, motion):
    ref = decode_motion_skeleton(
        motion[info["skeleton_segment_offset"]:info["first_clip_offset"]]
    )
    return ref


def triplet_stats(motion, info, joint_triplet_range):
    """Gather per-triplet statistics over every clip.

    Returns a dict keyed by triplet index with: max_abs, median_abs, animated
    ratio, and whether the value is invariant across clips.
    """
    boundaries = info["animation_boundaries"]
    stats = {}
    for start_trip in joint_triplet_range:
        key = start_trip
        stats[key] = {"max_abs": 0.0, "samples": [], "animated": 0, "clips": 0}
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start < 4:
            continue
        try:
            decoded = decode_scalar_clip(motion[start:end])
        except Exception:
            continue
        count = decoded["scalar_count"]
        for start_trip in stats:
            scalar = 8 + 3 * start_trip
            if scalar + 3 > count:
                continue
            entry = stats[start_trip]
            entry["clips"] += 1
            for track in decoded["tracks"][scalar:scalar + 3]:
                if track["mode"] in (6, 7):
                    entry["animated"] += 1
                    for value in track.get("values", ()):
                        entry["samples"].append(abs(value))
                        entry["max_abs"] = max(entry["max_abs"], abs(value))
                elif track["mode"] == 5:
                    for value in track.get("values", ()):
                        entry["samples"].append(abs(value))
                        entry["max_abs"] = max(entry["max_abs"], abs(value))
    return stats


def classify(stats):
    """Report the position/rotation signal of one triplet."""
    samples = sorted(stats["samples"])
    if not samples:
        return {"kind": "empty"}
    median = samples[len(samples) // 2]
    return {
        "max_abs": stats["max_abs"],
        "median_abs": median,
        "animated_tracks": stats["animated"],
        "clips": stats["clips"],
        # A rotation is bounded (<= ~pi, often small); a model-space position
        # routinely exceeds 2*pi and scales with the skeleton translation.
        "position_signal": stats["max_abs"] > 2.0 * math.pi + 0.01,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", default=["chr300", "chr301", "chr500"])
    args = parser.parse_args()
    for name in args.names:
        info, motion = load(name)
        ref = flag_table(info, motion)
        print(f"=== {name}: bones={ref['bone_count']} "
              f"ik_chains={ref['ik_chain_count']} "
              f"layout_valid={ref['ik_chain_layout_valid']}")
        flags = Counter(j["constraint_flags"] for j in ref["joints"])
        print("   constraint_flags:", dict(sorted(flags.items())))
        ik_joints = set()
        for chain in ref["ik_chains"]:
            ik_joints.update(chain["joint_indices"])
        print("   flagged IK joints:", sorted(ik_joints))
        print("   sample joints (id, parent, flags, ik_role, bind_translation):")
        for j in ref["joints"][:14]:
            t = j["translation"]
            print(f"     {j['index']:3d} id={j['bone_id']:3d} "
                  f"par={str(j['parent_index']):>4s} "
                  f"fl=0x{j['constraint_flags']:02x} "
                  f"{str(j['ik_role']):>12s} "
                  f"t=({t[0]:8.3f},{t[1]:8.3f},{t[2]:8.3f})")


if __name__ == "__main__":
    main()
