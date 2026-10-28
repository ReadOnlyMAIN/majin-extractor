"""Bridge from parsed motion resources to physical TRS animation segments."""
from __future__ import annotations

from pathlib import Path

from .package import parse_motion_package
from .resource import UnsupportedMotion, discover_motion_paths
from .sequence import duration_frames_of, parse_motion_sequence, parse_sequence_index

FPS_FALLBACK = 30  # verified clock: mode-7 tangents are per-second derivatives
# and the (v1-v0)/tangent ratio over one-frame key gaps measures 1/30 on the
# large humanoid packages (chr300 10305 gaps, chr540 115033 gaps —
# research/HUMANOID_LAYOUT_FINDINGS.md).


def decode_motion_baseline_clips(model_path: Path,
                                 frame_rate: float = FPS_FALLBACK) -> list[dict]:
    """Diagnostic frozen references, explicitly distinct from decoded motion.

    Kept only for --animations-mode baseline comparisons. These animations
    do not decode the physical curve or reconstruct a composite named state.
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
    from .transforms import local_reference_rotations
    quats = local_reference_rotations(skeleton)
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


def decode_character_animations(motion_info, skeleton=None, clip_indices=None,
                                *legacy_options, **kwargs) -> list[dict]:
    """Export physical curve segments through the compact TRS program.

    Five sparse sequence tables address package slots directly. A named state
    is exported as one segment when that slot has the same duration. Composite
    and missing states stay explicit in diagnostics; anonymous helpers retain
    stable segment names rather than inheriting another state's name.
    """
    from fnmatch import fnmatchcase
    from .pose import decode_transform_clip
    if not motion_info:
        return []
    selection = kwargs.get('animations', 'all')
    if selection in ('none', 'off'):
        return []
    package = parse_motion_package(Path(motion_info['package_path']))
    _header, records = parse_motion_sequence(Path(motion_info['sequence_path']))
    index = parse_sequence_index(Path(motion_info['sequence_path']))
    frame_rate = kwargs.get('frame_rate', FPS_FALLBACK)
    ik_mode = kwargs.get('ik_mode', 'bake')
    diagnostics = dict(mapping='compact_trs_program', rotation='Rz Ry Rx, radians, local absolute',
                       source_package=str(Path(motion_info['package_path']).resolve()),
                       source_sequence=str(Path(motion_info['sequence_path']).resolve()),
                       name_binding='physical_slot_and_duration',
                       state_index_decoded=True, segments=[], rejected=[],
                       unresolved_states=[],
                       rig_controls_raw=[record.hex() for record in package.rig_controls.records]
                       if package.rig_controls else [])
    motion_info['export_diagnostics'] = diagnostics
    animations = []
    patterns = [value.strip() for value in selection.split(',') if value.strip()]
    selected_slots = set(clip_indices) if clip_indices is not None else None
    resolved_records = set()
    record_slots = {record: slot for table in index.tables
                    for slot, record in enumerate(table) if record != 0xFFFFFFFF}
    blocks_by_slot = {block.slot_index: block for block in package.curve_blocks}
    for block in package.curve_blocks:
        slot = block.slot_index - 1
        name = f'segment_{slot:03d}'
        if selected_slots is not None and slot not in selected_slots:
            continue
        related = [records[i] for i in index.records_for_slot(block.slot_index)]
        duration = int.from_bytes(block.header[2:], 'big') - 1
        named = [record for record in related if duration_frames_of(record) == duration]
        names = [record.short_name for record in named] or [name]
        chosen = names if selection == 'all' else [
            candidate for candidate in names if any(
                fnmatchcase(candidate, pattern) or fnmatchcase(name, pattern)
                or str(slot) == pattern for pattern in patterns)]
        if not chosen:
            continue
        try:
            clip = decode_transform_clip(package, block, skeleton=skeleton,
                                         frame_rate=frame_rate, ik_mode=ik_mode)
        except (UnsupportedMotion, ValueError) as exc:
            diagnostics['rejected'].append(dict(slot=slot, reason=str(exc)))
            continue
        for chosen_name in chosen:
            record = next((r for r in named if r.short_name == chosen_name), None)
            if record is not None:
                resolved_records.add(record.index)
            output = dict(clip, name=chosen_name, name_source=(
                'sequence_slot_index_and_duration' if named else 'physical_segment'))
            animations.append(output)
            diagnostics['segments'].append({key: value for key, value in output.items()
                                            if key != 'channels'})
    instruction_groups = package.curve_index.groups if package.curve_index else []
    for record in records:
        if record.index not in resolved_records:
            package_slot = record_slots[record.index]
            block = blocks_by_slot.get(package_slot)
            frames = duration_frames_of(record)
            segment_frames = (int.from_bytes(block.header[2:], 'big') - 1
                              if block is not None else None)
            if package_slot >= len(package.slots):
                code, reason = 'outside_package', 'sequence slot is outside this package'
            elif package.slots[package_slot].size == 0:
                code, reason = 'zero_storage_span', 'slot has no own curve payload; repeated offset does not establish a name binding'
            elif block is None:
                code, reason = 'non_curve_slot', 'sequence slot does not contain a curve block'
            elif segment_frames != frames:
                code, reason = 'duration_mismatch', 'state duration differs from its physical segment; composition remains undecoded'
            elif any(item['slot'] == block.slot_index - 1 for item in diagnostics['rejected']):
                code, reason = 'decode_rejected', 'physical curve decoding failed; see rejected diagnostics'
            else:
                code, reason = 'not_selected', 'animation excluded by the requested selection'
            # Preserve the slot's instructions as evidence for future state
            # reconstruction. Empty storage does not establish a static pose.
            diagnostics['unresolved_states'].append(dict(
                record=record.index, name=record.short_name,
                duration_frames=frames, package_slot=package_slot,
                source_slot=package_slot - 1, segment_duration_frames=segment_frames,
                reason_code=code, reason=reason,
                slot_instructions_raw=(instruction_groups[package_slot - 1]
                                       if 0 < package_slot <= len(instruction_groups) else [])))
    return animations
