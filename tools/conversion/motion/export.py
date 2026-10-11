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
    """Decode the selected clips as glTF animation dicts (Phase 3).

    Evidence-gated: the package channel base is derived from bind-pose
    anchors (``curves.resolve_scalar_channel_base``); a curve block becomes
    a clip only when ``sample_count - 1`` equals a record's duration
    candidate (u32 at qstm+128) and every record/block pairing stays
    bijective — otherwise the function exports nothing rather than a
    guessed pose. Unanimated joints keep their bind pose in the GLB scene,
    so partial channels stay valid for Godot.
    """
    from .curves import decode_scalar_clip, resolve_scalar_channel_base
    if not motion_info or not motion_info.get("clip_count"):
        return []
    sequence_path = Path(motion_info["sequence_path"])
    package_path = Path(motion_info["package_path"])
    package = parse_motion_package(package_path)
    if package.skeleton is None or not package.curve_blocks:
        return []
    base = resolve_scalar_channel_base(package)
    if base is None:
        return []
    _header, records = parse_motion_sequence(sequence_path)
    frame_rate = kwargs.get("frame_rate", FPS_FALLBACK)
    clip_indices = clip_indices or None
    selections = [
        record for record in records
        if (duration_frames_of(record) or 0) > 0
        and (clip_indices is None
             or record.index in clip_indices)
    ]
    usable = []
    for record in selections:
        duration = duration_frames_of(record)
        if len(record.qstm.raw) < 132 and duration is None:
            duration = None
        usable.append((record, duration))
    primary_by_duration: dict[int, list] = {}
    for block in package.curve_blocks:
        try:
            decoded = decode_scalar_clip(package, block, base, frame_rate)
        except (UnsupportedMotion, ValueError):
            decoded = None
        if decoded is None:
            continue
        sample_minus_one = decoded["sample_count"] - 1
        primary_by_duration.setdefault(sample_minus_one, []).append(decoded)
    animations = []
    used: set[int] = set()
    for record, duration in usable:
        if duration is None:
            continue
        pool = primary_by_duration.get(duration, [])
        while len(pool) and id(pool[0]) in used:
            pool = pool[1:]
        if not pool:
            return []  # bijective pairing broken: no partial export
        clip = pool[0]
        used.add(id(clip))
        animations.append({
            "name": record.short_name,
            "duration": duration / frame_rate,
            "frame_rate": frame_rate,
            "channels": clip["channels"],
            "scalar_mapping": "resolved_from_bind_anchors",
            "resampled": clip["resampled"],
        })
    return animations