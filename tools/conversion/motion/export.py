"""Bridge between the clean motion package and the DDM export pipeline.

Phase 3 of REVERSE_MOTION.md wires curve decoding here; until that
research lands the bridge exports **no** animation (Evidence over
guessing): it only exposes the verified facts — clip names/durations and
the motion skeleton's bone ids in DDM transform order.
"""
from __future__ import annotations

from pathlib import Path

from .package import parse_motion_package
from .resource import UnsupportedMotion, discover_motion_paths
from .sequence import duration_frames_of, parse_motion_sequence

FPS_FALLBACK = 60  # documented fallback: grids are 8-frame aligned (REVERSE_MOTION.md)


def decode_motion_baseline_clips(model_path: Path,
                                 frame_rate: float = FPS_FALLBACK) -> list[dict]:
    """Export glTF animation dicts for every named clip of an asset.

    Practical variant (documented in REVERSE_MOTION.md 'Risques'):
    while the quantized curve stream resists decoding, each clip exports a
    *frozen baseline* — the joints hold the verified motion-skeleton bind
    pose over the duration hypothesis (u32 at qstm+128). Output dict keys
    match ``write_skinned_glb``'s glTF ``animations`` API, so the GLB
    cycle-check must never fail: full (valid) tracks per joint.
    """
    motion_info = discover_character_motion(model_path)
    if not motion_info or not motion_info.get("clip_count"):
        return []
    sequence_path, package_path = Path(motion_info["sequence_path"]), \
        Path(motion_info["package_path"])
    package = parse_motion_package(package_path)
    _header, records = parse_clip_records(sequence_path)
    if package.skeleton is None:
        return []
    skeleton = package.skeleton
    quats = list(skeleton.rotations)
    translations = list(skeleton.translations)
    animations = []
    for record in records:
        frames = duration_frames_of(record)
        if not frames or frames <= 0:
            continue
        times = [0.0, frames / frame_rate]
        channels = []
        for joint in range(skeleton.bone_count):
            channels.append({
                "joint": joint,
                "path": "translation",
                "times": times,
                "values": [list(translations[joint]) for _t in times],
                "interpolation": "STEP",
            })
            channels.append({
                "joint": joint,
                "path": "rotation",
                "times": times,
                "values": [list(quats[joint]) for _t in times],
                "interpolation": "STEP",
            })
        animations.append({
            "name": record.short_name,
            "duration": frames / frame_rate,
            "frame_rate": frame_rate,
            "baseline": True,          # visible marker: not the timed curves
            "channels": channels,
        })
    return animations


def discover_character_motion(model_path: Path) -> dict | None:
    """Locate and validate the motion resources next to a character DDM.

    Returns ``None`` when no matching pair exists next to the model, else a
    dict summarising the verified motion data (paths, clip list, bone ids).
    """
    model_path = Path(model_path)
    kb_root = next(
        (parent for parent in model_path.parents
         if parent.name.lower() == "kb"), None,
    )
    if kb_root is None:
        return None
    name = model_path.stem
    paths = discover_motion_paths(kb_root, name)
    if paths is None:
        return None
    sequence_path, package_path = paths
    motion: dict = {
        "sequence_path": str(sequence_path),
        "package_path": str(package_path),
        "clip_count": None,
        "clip_names": [],
        "skeleton_bone_ids": None,
    }
    try:
        _header, records = parse_clip_records(sequence_path)
    except (UnsupportedMotion, OSError):
        records = None
    if records is not None:
        motion["clip_count"] = len(records)
        motion["clip_names"] = [clip.short_name for clip in records]
    try:
        package = parse_motion_package(package_path)
    except (UnsupportedMotion, OSError):
        package = None
    if package is not None and package.skeleton is not None:
        # The motion skeleton lists its bone ids in the DDM transform order
        # (verified across the corpus); the DDM skeleton decoder uses that
        # order as a cross-check of its own transform list.
        motion["skeleton_bone_ids"] = list(package.skeleton.bone_ids)
    return motion


def parse_clip_records(sequence_path: Path):
    from .sequence import parse_motion_sequence
    return parse_motion_sequence(sequence_path)


def decode_character_animations(motion_info, skeleton, clip_indices=None,
                                *args, **kwargs) -> list[dict]:
    """Decode the selected clips as glTF animation dicts.

    REVERSE_MOTION.md Phase 2 research: no verified joint-by-joint timed
    curve exists yet, so this STILL returns an empty list. See
    ``decode_motion_baseline_clips`` for the documented frozen-baseline
    export (explicitly tagged with ``baseline=True``).
    """
    return []