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

try:
    from ddm.binary import UnsupportedDDMVariant
except ImportError:  # pragma: no cover - package import
    from tools.conversion.ddm.binary import UnsupportedDDMVariant


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

    Curve decoding is still under active research (REVERSE_MOTION.md
    Phase 2), so this returns an empty list — the pipeline exports the
    skinned mesh with its joints but no guessed animation.
    """
    return []