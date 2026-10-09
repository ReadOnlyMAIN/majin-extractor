#!/usr/bin/env python3
"""Diagnostic probe for character motion scalar layouts.

This is a research tool, not part of the maintained pipeline. It reuses the
validated storage decoder in ``tools/conversion/motion_decode.py`` and prints
factual comparisons of the eight leading "root" scalars across clips and
characters. Its goal is to confirm or reject interpretations of scalar slots
0..7 without guessing: the same clips are shared between the chr30x humanoids,
so any slot meaning must reproduce across them.

Usage::

    python research/motion_probe.py chr301 --clips root
    python research/motion_probe.py chr301 chr300 chr302 --clips compare
    python research/motion_probe.py chr301 --clips turn
    python research/motion_probe.py chr301 --clips all
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "conversion"))

from motion_decode import (  # noqa: E402
    UnsupportedMotion,
    decode_motion_skeleton,
    decode_scalar_clip,
    discover_character_motion,
    infer_humanoid_joint_scalar_starts,
    remap_humanoid_scalar_starts,
    _sample_linear,
)

KB = Path(__file__).resolve().parents[1] / "game_files/decompressed/KB"


def character_path(name: str) -> Path:
    return KB / "chara" / name / name


def load(name: str):
    info = discover_character_motion(character_path(name))
    motion = Path(info["sequence_path"]).read_bytes()
    return info, motion


def sample(track, frame):
    fallback = 1.0 if track["mode"] == 3 else -1.0 if track["mode"] == 4 else 0.0
    return _sample_linear(track, frame, fallback)


def describe_clip(info, motion, index):
    bounds = info["animation_boundaries"]
    start, end = bounds[index], bounds[index + 1]
    if end <= start:
        return None
    decoded = decode_scalar_clip(motion[start:end])
    return start, end, decoded


def root_table(info, motion, span=None):
    """Print frame-0 / frame-end values for scalar slots 0..7 of each clip."""
    bounds = info["animation_boundaries"]
    slots = info.get("clip_name_slots", [])
    indices = range(len(bounds) - 1) if span is None else span
    print(f"{'idx':>4} {'name':32s} {'n':>4} " + " ".join(
        f"s{j:<15}" for j in range(8)
    ))
    for i in indices:
        if i >= len(bounds) - 1:
            continue
        row = describe_clip(info, motion, i)
        if row is None:
            continue
        _, _, d = row
        n = d["frame_count"]
        cells = []
        for j in range(min(8, d["scalar_count"])):
            t = d["tracks"][j]
            cells.append(f"{sample(t,0):+.3f}>{sample(t,n-1):+.3f}".ljust(15))
        name = (slots[i] if i < len(slots) else None) or "-"
        print(f"{i:>4} {name[:32]:32s} {n:>4} " + " ".join(cells))


def index_by_name(info, clip_name):
    """Resolve a clip name to its slot index within one character.

    Names carry a per-character prefix (``c300_``/``c301_``), so match on the
    trailing action suffix when an exact name is not present.
    """
    slots = info.get("clip_name_slots", [])
    for i, name in enumerate(slots):
        if name == clip_name:
            return i
    for i, name in enumerate(slots):
        if name and name.endswith(clip_name):
            return i
    return None


def compare_characters(names, clip_names):
    """Show which clips carry identical scalar data across characters.

    Clips are matched by NAME, never by raw index: chr301 stores the shared
    humanoid clips four slots earlier than chr300/302/303, so an index-based
    comparison silently lines up different animations.
    """
    loaded = {name: load(name) for name in names}
    print("Comparing named clips", clip_names, "across", names)
    for clip_name in clip_names:
        print(f"\n--- {clip_name} ---")
        for name, (info, motion) in loaded.items():
            i = index_by_name(info, clip_name)
            if i is None:
                print(f"  {name}: clip not present")
                continue
            row = describe_clip(info, motion, i)
            if row is None:
                print(f"  {name}[slot {i}]: empty")
                continue
            _, _, d = row
            n = d["frame_count"]
            cells = []
            for j in range(min(8, d["scalar_count"])):
                t = d["tracks"][j]
                cells.append(f"s{j}[{sample(t,0):+.3f}->{sample(t,n-1):+.3f}]")
            print(f"  {name}[slot {i}]: n={n:3d} scalars={d['scalar_count']:3d} "
                  + " ".join(cells))


def analyze_slot_identity(info, motion):
    """Check the claim that slot 6 duplicates slot 0.

    A true duplication gives a zero, constant difference across every frame of
    every clip. A non-constant difference rejects the claim.
    """
    bounds = info["animation_boundaries"]
    slots = info.get("clip_name_slots", [])
    identical = 0
    total = 0
    worst = (0.0, None)
    for i in range(len(bounds) - 1):
        row = describe_clip(info, motion, i)
        if row is None:
            continue
        _, _, d = row
        if d["scalar_count"] < 7:
            continue
        total += 1
        n = d["frame_count"]
        t0, t6 = d["tracks"][0], d["tracks"][6]
        diffs = [sample(t6, f) - sample(t0, f) for f in range(n)]
        spread = max(diffs) - min(diffs)
        if spread <= 1e-4 and abs(diffs[0]) <= 1e-4:
            identical += 1
        if spread > worst[0]:
            worst = (spread, slots[i] if i < len(slots) else f"motion_{i:03d}")
    print(f"clips analysed            : {total}")
    print(f"clips with slot6 == slot0 : {identical}")
    print(f"worst slot6-slot0 spread  : {worst[0]:.4f} rad on {worst[1]}")


def _reference_skeleton(info, motion):
    """Decode the ordered reference skeleton embedded in the motion stream."""
    start = info.get("skeleton_segment_offset")
    end = info.get("first_clip_offset")
    if start is None or end is None:
        raise UnsupportedMotion("Motion stream carries no reference skeleton")
    return decode_motion_skeleton(motion[start:end])


def character_structure(info, motion):
    """Summarise the coherent structural patterns of one character's stream.

    Returns the ordered scalar-count variants, whether the canonical count is
    triplet-aligned after the eight root scalars, and the skeleton-driven
    binding of joint triplets versus the interleaved auxiliary rig controls.
    Everything is computed from the binary; nothing is inferred from names.
    """
    bounds = info["animation_boundaries"]
    counts = [
        struct.unpack_from(">H", motion, start)[0]
        for start, end in zip(bounds, bounds[1:])
        if end - start >= 2
    ]
    variants = Counter(counts)
    canonical = max(counts)
    report = {
        "clip_count": len(bounds) - 1,
        "nonempty_clip_count": len(counts),
        "scalar_count_variants": dict(sorted(variants.items())),
        "canonical_scalar_count": canonical,
        "root_scalar_count": 8,
        "skeleton_version": None,
        "bone_count": None,
        "triplet_aligned": (canonical - 8) % 3 == 0,
        "triplet_count": None,
        "joint_triplet_count": None,
        "auxiliary_triplet_count": None,
    }
    try:
        skeleton = _reference_skeleton(info, motion)
    except UnsupportedMotion as exc:
        report["skeleton_error"] = str(exc)
        return report
    report["skeleton_version"] = skeleton["version"]
    report["bone_count"] = skeleton["bone_count"]
    if not report["triplet_aligned"]:
        report["triplet_remainder"] = (canonical - 8) % 3
        return report
    joints = [
        {"global_id": bone_id, "index": index}
        for index, bone_id in enumerate(skeleton["bone_ids"])
    ]
    starts, layout = infer_humanoid_joint_scalar_starts(
        {"joints": joints}, canonical,
    )
    bound = sorted(starts.values())
    bound_set = set(bound)
    auxiliary = [
        start for start in range(8, canonical, 3) if start not in bound_set
    ]
    report.update(
        triplet_count=layout["triplet_count"],
        joint_triplet_count=layout["joint_triplet_count"],
        auxiliary_triplet_count=layout["auxiliary_triplet_count"],
        accessory_auxiliary_triplet_count=layout.get(
            "accessory_auxiliary_triplet_count",
        ),
        unbound_accessory_joint_count=layout.get(
            "unbound_accessory_joint_count",
        ),
        unbound_accessory_triplet_count=layout.get(
            "unbound_accessory_triplet_count",
        ),
        auxiliary_group_starts=auxiliary,
    )
    # Auxiliary groups are interleaved by anatomical segment. Record the first
    # auxiliary start seen after each bound joint so the interleaving pattern is
    # machine-checkable (the leading three controls always sit after the trunk).
    report["leading_auxiliary_starts"] = auxiliary[:3]
    return report


def bound_triplets_named(name, action, loaded, structures):
    """Return every bound joint curve of one named clip, keyed by bone ID.

    ``action`` is the clip name without the per-character ``c30x_`` prefix, so
    the same call resolves across characters.  Curves are flattened to a rounded
    tuple per bone so equality checks are exact and order-independent of the
    track indices, which differ between characters.
    """
    info, motion = loaded[name]
    structure = structures[name]
    if not structure.get("triplet_aligned"):
        return None
    index = index_by_name(info, action)
    if index is None:
        return None
    row = describe_clip(info, motion, index)
    if row is None:
        return None
    _, _, decoded = row
    skeleton = _reference_skeleton(info, motion)
    joints = [
        {"global_id": bone_id, "index": joint_index}
        for joint_index, bone_id in enumerate(skeleton["bone_ids"])
    ]
    starts, _ = infer_humanoid_joint_scalar_starts(
        {"joints": joints}, structure["canonical_scalar_count"],
    )
    remapped = remap_humanoid_scalar_starts(
        {"joints": joints}, starts, decoded["scalar_count"],
        structure["canonical_scalar_count"],
    )
    curves = {}
    for joint_index, bone_id in enumerate(skeleton["bone_ids"]):
        start = remapped.get(joint_index)
        if start is None or start + 2 >= decoded["scalar_count"]:
            continue
        curves[bone_id] = tuple(
            round(sample(decoded["tracks"][start + axis], frame), 5)
            for axis in range(3)
            for frame in range(decoded["frame_count"])
        )
    return curves


def compare_structure(names):
    """Compare the structural patterns of several humanoid characters.

    Prints one block per character, then verifies that named shared clips carry
    byte-identical *bound* triplets.  Bound joint triplets of the shared
    humanoid clips must match across characters once clips are aligned by name;
    auxiliary control blocks differ because of the extra accessory chains.
    """
    loaded = {name: load(name) for name in names}
    print("== structural patterns ==")
    structures = {}
    for name, (info, motion) in loaded.items():
        structure = character_structure(info, motion)
        structures[name] = structure
        print(f"\n--- {name} ---")
        for key in (
            "clip_count", "nonempty_clip_count", "scalar_count_variants",
            "canonical_scalar_count", "skeleton_version", "bone_count",
            "triplet_aligned", "triplet_remainder", "triplet_count",
            "joint_triplet_count", "auxiliary_triplet_count",
            "accessory_auxiliary_triplet_count",
            "unbound_accessory_joint_count",
            "unbound_accessory_triplet_count", "leading_auxiliary_starts",
        ):
            if key in structure:
                print(f"  {key:36s}: {structure[key]}")

    # Alignment check on the eight leading root scalars of a shared clip. The
    # root scalars are stored identically across chr30x, so a mismatch flags a
    # decoding error rather than a legitimate layout difference.
    print("\n== shared root-scalar alignment ==")
    clip_name = "0010_turn_180_l_a_00"
    for name, (info, motion) in loaded.items():
        i = index_by_name(info, clip_name)
        if i is None:
            print(f"  {name}: clip not present")
            continue
        row = describe_clip(info, motion, i)
        if row is None:
            print(f"  {name}: empty")
            continue
        _, _, d = row
        n = d["frame_count"]
        root = [
            round(sample(d["tracks"][j], f), 6)
            for j in range(min(8, d["scalar_count"]))
            for f in (0, n - 1)
        ]
        print(f"  {name}[slot {i}]: {root}")

    # Bound-triplet check: for a shared clip, every bone present in several
    # characters must carry the same curve. Track indices differ by character
    # (skeleton sizes differ), so the curves are compared after the skeleton
    # binding, keyed by bone ID.
    reference_name = next(
        (name for name in names if structures[name].get("triplet_aligned")),
        None,
    )
    if reference_name is None:
        print("\n== bound-triplet comparison ==\n  no triplet-aligned reference")
        return

    def bound_triplets(name, action):
        return bound_triplets_named(name, action, loaded, structures)

    print(f"\n== bound-triplet comparison on {clip_name} ==")
    reference_curves = bound_triplets(reference_name, clip_name)
    if not reference_curves:
        print(f"  {reference_name}: no bound curves")
        return
    for name in names:
        curves = bound_triplets(name, clip_name)
        if curves is None:
            print(f"  {name}: not triplet-aligned (skipped)")
            continue
        shared = sorted(set(reference_curves) & set(curves))
        mismatched = [
            bone_id for bone_id in shared
            if reference_curves[bone_id] != curves[bone_id]
        ]
        print(
            f"  {name}: {len(shared)} shared bones, "
            f"{len(mismatched)} mismatched vs {reference_name}"
            + (f" (e.g. {mismatched[:6]})" if mismatched else "")
        )

    # Whole-stream aggregate: match every action suffix of the reference
    # against the others and count how many shared clips carry an identical
    # bound curve set. The action suffix (the part after the c30x_ prefix) is
    # the cross-character key; raw clip index and the prefix both differ.
    reference_slots = loaded[reference_name][0].get("clip_name_slots", [])
    actions = sorted({
        name.split("_", 1)[1] for name in reference_slots if name
    })
    print(
        f"\n== shared-action aggregate ({len(actions)} actions on "
        f"{reference_name}) =="
    )
    for name in names:
        if name == reference_name:
            continue
        total = identical = 0
        for action in actions:
            reference_clip = bound_triplets(reference_name, action)
            other_clip = bound_triplets(name, action)
            if not reference_clip or not other_clip:
                continue
            shared = set(reference_clip) & set(other_clip)
            total += 1
            if all(reference_clip[b] == other_clip[b] for b in shared):
                identical += 1
        print(
            f"  {reference_name} vs {name}: {identical}/{total} shared-action "
            "clips identical on shared bones"
        )


def main():
    ap = argparse.ArgumentParser(description="Motion scalar layout probe")
    ap.add_argument("characters", nargs="+")
    ap.add_argument(
        "--clips", default="root",
        choices=["root", "turn", "all", "compare", "identity", "structure"],
    )
    args = ap.parse_args()

    if args.clips == "compare":
        compare_characters(args.characters, [
            "0010_turn_180_l_a_00",
            "0010_turn_180_l_b_00",
            "0010_turn_90_l_a_00",
            "0010_turn_90_r_a_00",
        ])
        return

    if args.clips == "structure":
        compare_structure(args.characters)
        return

    for name in args.characters:
        info, motion = load(name)
        slots = info.get("clip_name_slots", [])
        if args.clips == "identity":
            print(f"=== {name} ===")
            analyze_slot_identity(info, motion)
        elif args.clips == "root":
            print(f"=== {name} ===")
            root_table(info, motion, span=range(0, 12))
        elif args.clips == "turn":
            idx = [i for i, s in enumerate(slots) if s and "turn" in s]
            print(f"=== {name} turn clips {idx} ===")
            root_table(info, motion, span=idx)
        elif args.clips == "all":
            print(f"=== {name} ===")
            root_table(info, motion)


if __name__ == "__main__":
    main()
