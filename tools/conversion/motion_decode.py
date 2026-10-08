#!/usr/bin/env python3
"""Discovery and decoding entry point for character motion resources.

DDM parsing owns meshes, materials, and skin weights. This module owns the
separate motionSequence/motionPackage formats and returns data without any
glTF-specific representation. Animation track decoding will be added here as
the format is reverse engineered.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import struct
from collections import Counter


def _be_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def decode_character_rig_graph(data: bytes):
    """Decode the fixed-size records in a character ``.crg`` rig graph.

    Field semantics are still under investigation, so integer selectors,
    referenced bone IDs, float parameters, and trailing flags are kept
    separate without assigning constraint names.  chr300 stores eight
    128-byte records after its 136-byte header.
    """
    if len(data) < 0x88 or data[:4] != b"\0crg":
        raise UnsupportedMotion("Invalid character rig graph")
    boundary_count = _be_u32(data, 8)
    record_count = _be_u32(data, 0x80)
    expected_size = 0x88 + record_count * 0x80
    if boundary_count != record_count + 1 or expected_size != len(data):
        raise UnsupportedMotion(
            f"Unexpected character rig graph size/counts: "
            f"{len(data)}, {boundary_count}, {record_count}"
        )
    records = []
    for index in range(record_count):
        offset = 0x88 + index * 0x80
        words = struct.unpack_from(">32I", data, offset)
        parameters = struct.unpack_from(">15f", data, offset + 8 * 4)
        records.append({
            "index": index,
            "offset": offset,
            "selector": words[0],
            "bone_id_a": None if words[1] == 0xFFFFFFFF else words[1],
            "bone_id_b": None if words[2] == 0xFFFFFFFF else words[2],
            "kind": words[3],
            "configuration": list(words[4:8]),
            "parameters": list(parameters),
            "flags": list(words[23:]),
        })
    return {
        "boundary_count": boundary_count,
        "record_count": record_count,
        "record_size": 0x80,
        "records": records,
    }


def decode_motion_metadata(segment: bytes):
    """Decode the per-clip record table in the final motion segment.

    The first word is the clip count and the following offsets are relative to
    this segment.  Record payload semantics are not named prematurely: the
    common leading words are exposed along with any variable event bytes.
    """
    if len(segment) < 8:
        raise UnsupportedMotion("Truncated motion metadata")
    count = _be_u32(segment, 0)
    table_end = 4 + 4 * count
    if not count or table_end > len(segment):
        raise UnsupportedMotion("Invalid motion metadata count")
    offsets = list(struct.unpack_from(f">{count}I", segment, 4))
    if offsets != sorted(offsets) or any(
        offset < table_end or offset > len(segment) for offset in offsets
    ):
        raise UnsupportedMotion("Invalid motion metadata offsets")
    records = []
    for index, start in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < count else len(segment)
        payload = segment[start:end]
        if len(payload) < 8:
            raise UnsupportedMotion("Truncated motion metadata record")
        records.append({
            "clip_index": index,
            "offset": start,
            "size": len(payload),
            "flags": _be_u32(payload, 0),
            "secondary": _be_u32(payload, 4),
            "event_data": list(payload[8:]),
        })
    return {"record_count": count, "records": records}


def decode_motion_skeleton(segment: bytes):
    """Decode the animation resource's ordered reference skeleton.

    The segment begins with a two-byte header and one two-byte hierarchy entry
    for every non-root bone. It is followed by the ordered byte-sized bone IDs,
    two-byte alignment, then local translation/quaternion records.
    """
    if len(segment) < 4:
        raise UnsupportedMotion("Truncated motion skeleton")
    bone_count, version = segment[0], segment[1]
    if not 1 <= bone_count <= 255:
        raise UnsupportedMotion("Invalid motion skeleton bone count")
    ids_offset = 2 + (bone_count - 1) * 2
    pose_offset = (ids_offset + bone_count + 3) & ~3
    expected_end = pose_offset + bone_count * 28
    if expected_end != len(segment):
        raise UnsupportedMotion(
            f"Unexpected motion skeleton size: {len(segment)} != {expected_end}"
        )
    bone_ids = list(segment[ids_offset:ids_offset + bone_count])
    if len(set(bone_ids)) != bone_count:
        raise UnsupportedMotion("Duplicate motion skeleton bone ID")
    hierarchy = [
        tuple(segment[2 + index * 2:4 + index * 2])
        for index in range(bone_count - 1)
    ]
    joints = []
    for index, bone_id in enumerate(bone_ids):
        values = struct.unpack_from(">7f", segment, pose_offset + index * 28)
        translation, rotation = values[:3], values[3:]
        if not all(math.isfinite(value) for value in values):
            raise UnsupportedMotion("Non-finite motion reference transform")
        norm = math.sqrt(sum(value * value for value in rotation))
        if not 0.99 <= norm <= 1.01:
            raise UnsupportedMotion("Invalid motion reference quaternion")
        joints.append({
            "index": index,
            "bone_id": bone_id,
            "parent_index": None if index == 0 else hierarchy[index - 1][0],
            "hierarchy_flags": 0 if index == 0 else hierarchy[index - 1][1],
            "translation": translation,
            "rotation": tuple(value / norm for value in rotation),
        })
    return {
        "version": version,
        "bone_count": bone_count,
        "bone_ids": bone_ids,
        "hierarchy": hierarchy,
        "joints": joints,
    }


def discover_character_motion(model_path: Path):
    """Locate and validate motion resources associated with a character DDM."""
    model_path = Path(model_path)
    kb_root = next(
        (parent for parent in model_path.parents if parent.name.lower() == "kb"),
        None,
    )
    if kb_root is None:
        return None

    name = model_path.stem
    state_sequence_path = kb_root / "motionSequence" / name / name
    curve_package_path = kb_root / "motionPackage" / name / "BigEndian" / name
    # Correctly indexed archives put the curve stream in motionPackage.
    # Older extractions shifted names by one and put it in motionSequence.
    sequence_path = curve_package_path
    if not sequence_path.is_file() or sequence_path.read_bytes()[:4] != b"\0\0\0\0":
        sequence_path = state_sequence_path
    package_path = model_path.with_name(model_path.name + ".crg")
    if not package_path.is_file():
        package_path = curve_package_path
    result = {
        "sequence_path": str(sequence_path) if sequence_path.is_file() else None,
        "package_path": str(package_path) if package_path.is_file() else None,
        "clip_count": 0,
        "package_entry_count": None,
        "decoded": False,
        "state_sequence_path": str(state_sequence_path) if state_sequence_path.is_file() else None,
    }

    if state_sequence_path.is_file():
        state_data = state_sequence_path.read_bytes()
        if len(state_data) >= 0x84 and state_data[:4] == b"psmr":
            slot_count = _be_u32(state_data, 0x80)
            table_end = 0x84 + slot_count * 4
            if 0 < slot_count <= 100_000 and table_end <= len(state_data):
                name_table = [
                    match.group(1).decode("ascii")
                    for match in re.finditer(
                        rb"KB/motionSequence/[^/\x00]+/([A-Za-z0-9_]+)",
                        state_data,
                    )
                ]
                indices = struct.unpack_from(f">{slot_count}I", state_data, 0x84)
                if all(index == 0xFFFFFFFF or index < len(name_table)
                       for index in indices):
                    result["clip_name_slots"] = [
                        None if index == 0xFFFFFFFF else name_table[index]
                        for index in indices
                    ]
                    result["named_clip_count"] = sum(
                        index != 0xFFFFFFFF for index in indices
                    )

    if sequence_path.is_file():
        motion = sequence_path.read_bytes()
        if len(motion) >= 0x88:
            boundary_count = _be_u32(motion, 0x80)
            table_end = 0x84 + boundary_count * 4
            if 1 < boundary_count <= 100_000 and table_end <= len(motion):
                offsets = struct.unpack_from(
                    ">" + "I" * boundary_count, motion, 0x84,
                )
                boundaries = [0x80 + offset for offset in offsets]
                if boundaries == sorted(boundaries) and all(
                    table_end <= offset <= len(motion) for offset in boundaries
                ):
                    segment_count = boundary_count - 1
                    result["segment_count"] = segment_count
                    result["clip_count"] = segment_count
                    result["clip_boundaries"] = boundaries
                    result["first_clip_offset"] = boundaries[0]
                    result["last_clip_offset"] = boundaries[-2]
                    result["sequence_end_offset"] = boundaries[-1]
                    # Observed character sequences wrap the actual animations
                    # in a skeleton segment and a final metadata segment. The
                    # metadata begins with the animation count.
                    metadata_offset = boundaries[-2]
                    if metadata_offset + 4 <= boundaries[-1]:
                        declared_animations = _be_u32(motion, metadata_offset)
                        if (
                            segment_count >= 3
                            and declared_animations > 0
                            and declared_animations == segment_count - 2
                        ):
                            result["clip_count"] = declared_animations
                            result["skeleton_segment_offset"] = boundaries[0]
                            result["first_clip_offset"] = boundaries[1]
                            result["last_clip_offset"] = boundaries[-3]
                            result["metadata_segment_offset"] = metadata_offset
                            result["animation_boundaries"] = boundaries[1:-1]
                            try:
                                metadata = decode_motion_metadata(
                                    motion[metadata_offset:boundaries[-1]]
                                )
                                result["clip_metadata"] = metadata["records"]
                            except UnsupportedMotion as exc:
                                result["clip_metadata_error"] = str(exc)
                    first_clip = boundaries[0]
                    bone_count = motion[first_clip]
                    bone_ids_offset = first_clip + bone_count * 2
                    bone_ids_end = bone_ids_offset + bone_count
                    if (
                        bone_count > 0
                        and bone_ids_end <= boundaries[1]
                        and len(set(motion[bone_ids_offset:bone_ids_end]))
                            == bone_count
                    ):
                        # The first clip supplies the canonical transform order.
                        # DDM bone IDs and parent IDs use another order.
                            result["skeleton_bone_ids"] = list(
                                motion[bone_ids_offset:bone_ids_end]
                            )
                    if result.get("skeleton_segment_offset") is not None:
                        try:
                            reference = decode_motion_skeleton(
                                motion[boundaries[0]:boundaries[1]]
                            )
                            result["motion_skeleton_version"] = reference["version"]
                            result["motion_skeleton_bone_count"] = reference["bone_count"]
                            result["skeleton_bone_ids"] = reference["bone_ids"]
                        except UnsupportedMotion as exc:
                            result["motion_skeleton_error"] = str(exc)

    if package_path.is_file():
        package = package_path.read_bytes()
        if len(package) >= 12 and package[:4] == b"\x00crg":
            try:
                rig_graph = decode_character_rig_graph(package)
                result["package_boundary_count"] = rig_graph["boundary_count"]
                result["package_entry_count"] = rig_graph["record_count"]
                result["rig_graph"] = rig_graph
            except UnsupportedMotion as exc:
                result["rig_graph_error"] = str(exc)

    if not result["sequence_path"] and not result["package_path"]:
        return None
    return result


class UnsupportedMotion(ValueError):
    """The source cannot yet be decoded without guessing its interpretation."""


def decode_scalar_clip(clip: bytes):
    """Decode scalar storage, independently of the unresolved rig binding.

    The header counts scalar descriptors, not bones. Eight 3-bit descriptors
    are packed into a big-endian 24-bit word, least significant descriptor
    first. Mode 5 is constant, 6 has linear samples, and 7 value/tangent pairs.
    Codes 0..4 are retained symbolically until their rig-specific meaning is
    established. No magnitude filters, made-up quaternions or bind offsets.
    """
    if len(clip) < 4:
        raise UnsupportedMotion("Truncated clip header")
    count, frame_count = struct.unpack_from(">HH", clip)
    if not count or frame_count < 2:
        raise UnsupportedMotion("Invalid scalar/frame count")
    descriptor_end = 4 + ((count + 7) // 8) * 3
    if descriptor_end > len(clip):
        raise UnsupportedMotion("Truncated scalar descriptor table")
    modes = []
    for offset in range(4, descriptor_end, 3):
        packed = int.from_bytes(clip[offset:offset + 3], "big")
        modes.extend((packed >> (index * 3)) & 7 for index in range(8))
    if any(modes[count:]):
        raise UnsupportedMotion("Nonzero descriptor padding")
    modes = modes[:count]
    cursor = descriptor_end
    key_frames = {}
    if frame_count > 256:
        raise UnsupportedMotion("Wide key-time encoding is not implemented")
    for index, mode in enumerate(modes):
        if mode not in (6, 7):
            continue
        if cursor >= len(clip):
            raise UnsupportedMotion("Truncated key table")
        interior_count = clip[cursor]
        cursor += 1
        if cursor + interior_count > len(clip):
            raise UnsupportedMotion("Truncated key times")
        interior = list(clip[cursor:cursor + interior_count])
        cursor += interior_count
        if interior != sorted(set(interior)) or any(
            not 0 < frame < frame_count - 1 for frame in interior
        ):
            raise UnsupportedMotion("Invalid interior key times")
        key_frames[index] = [0, *interior, frame_count - 1]
    payload_start = (cursor + 3) & ~3
    if payload_start > len(clip):
        raise UnsupportedMotion("Invalid key-table alignment")
    # The writer does not clear alignment bytes consistently.  Several valid
    # chr300 clips retain nonzero bytes here, while their aligned float payload
    # still accounts for the segment exactly.
    key_table_padding = list(clip[cursor:payload_start])
    cursor = payload_start
    tracks = []
    for index, mode in enumerate(modes):
        frames = key_frames.get(index, [0])
        words = (1 if mode == 5 else len(frames) if mode == 6
                 else 2 * len(frames) if mode == 7 else 0)
        if cursor + words * 4 > len(clip):
            raise UnsupportedMotion("Truncated curve payload")
        values = list(struct.unpack_from(f">{words}f", clip, cursor))
        if not all(math.isfinite(value) for value in values):
            raise UnsupportedMotion("Non-finite curve data")
        track = {"scalar_index": index, "mode": mode,
                 "payload_offset": cursor, "payload_size": words * 4}
        if words:
            track.update(frames=frames, values=values[::2] if mode == 7 else values)
            if mode == 7:
                track["tangents"] = values[1::2]
        tracks.append(track)
        cursor += words * 4
    if cursor != len(clip):
        raise UnsupportedMotion(f"Unaccounted curve bytes: {len(clip) - cursor}")
    return {"scalar_count": count, "frame_count": frame_count,
            "payload_offset": payload_start, "bytes_consumed": cursor,
            "key_table_padding": key_table_padding,
            "tracks": tracks}


def _sample_linear(track, frame, fallback):
    if track["mode"] == 5:
        return track["values"][0]
    if track["mode"] not in (6, 7):
        return fallback
    frames, values = track["frames"], track["values"]
    for right in range(1, len(frames)):
        if frame <= frames[right]:
            left = right - 1
            span = frames[right] - frames[left]
            alpha = (frame - frames[left]) / span
            return values[left] * (1 - alpha) + values[right] * alpha
    return values[-1]


def decode_experimental_joint_rotations(decoded, skeleton, joint_indices,
                                        fps=30.0, angle_units="radians",
                                        group_start=None,
                                        scalar_starts=None,
                                        reference_skeleton=None,
                                        deforming_joint_indices=None,
                                        axis_map="xyz", axis_signs="+++",
                                        rotation_model="global_delta",
                                        reference_decoded=None,
                                        reference_frame=None,
                                        active_joint_indices=None,
                                        reference_scalar_starts=None):
    """Decode selected Euler triplets into local joint rotations.

    Motion layouts end in three Euler scalars per reference-skeleton joint,
    preceded by rig-dependent controller data. Cross-character comparison of
    shared clips and bone IDs establishes the triplet stride even when the
    same bone occurs at a different skeleton index. The motion reference
    quaternions match the accumulated
    global DDM bind rotations, not the DDM joints' local rotations. The legacy
    probe applies curves around that global reference. Structurally bound
    humanoid curves can instead be composed as deltas in local bind space.
    """
    def multiply(a, b):
        x1, y1, z1, w1 = a
        x2, y2, z2, w2 = b
        return (
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2,
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
        )

    if angle_units not in ("degrees", "radians", "auto", "adaptive"):
        raise ValueError(f"Unknown angle units: {angle_units}")
    axis_map = axis_map.casefold()
    if sorted(axis_map) != ["x", "y", "z"]:
        raise ValueError(f"Invalid rotation axis map: {axis_map}")
    if len(axis_signs) != 3 or any(sign not in "+-" for sign in axis_signs):
        raise ValueError(f"Invalid rotation axis signs: {axis_signs}")
    if rotation_model not in (
        "global_delta", "global_delta_active", "global_delta_row",
        "global_delta_row_inverse",
        "local_absolute", "local_delta_post", "local_delta_pre",
        "global_reference_active", "global_reference_active_inverse",
        "global_reference_row", "global_reference_row_inverse",
    ):
        raise ValueError(f"Invalid rotation model: {rotation_model}")
    if rotation_model.startswith("global_reference_") and reference_decoded is None:
        raise ValueError(f"{rotation_model} requires a rotation reference clip")

    def axis_quaternion(axis, angle, units):
        half = (math.radians(angle) if units == "degrees" else angle) * 0.5
        result = [0.0, 0.0, 0.0, math.cos(half)]
        result[axis] = math.sin(half)
        return tuple(result)

    def inverse(quaternion):
        return (-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3])

    joints = skeleton.get("joints", [])
    if scalar_starts is None and group_start is None:
        group_start = decoded["scalar_count"] - 3 * len(joints)
    if scalar_starts is None and not 0 <= group_start <= decoded["scalar_count"]:
        raise UnsupportedMotion(
            f"Invalid rotation group start {group_start} for "
            f"{decoded['scalar_count']} scalars"
        )
    if any(not 0 <= joint_index < len(joints) for joint_index in joint_indices):
        raise UnsupportedMotion("Rotation joint index is out of range")

    reference_joints = (
        reference_skeleton.get("joints", []) if reference_skeleton else []
    )
    if reference_joints and len(reference_joints) != len(joints):
        raise UnsupportedMotion("Motion and DDM skeleton sizes do not match")

    # Fall back to accumulated DDM rotations for synthetic/unit-test rigs.
    global_bind = []
    for index, joint in enumerate(joints):
        if reference_joints:
            quaternion = tuple(reference_joints[index]["rotation"])
        else:
            parent = joint.get("parent")
            quaternion = tuple(joint["rotation"])
            if parent is not None:
                quaternion = multiply(global_bind[parent], quaternion)
        global_bind.append(quaternion)

    def collect_tracks(source_decoded, starts):
        result = []
        for joint_index in range(len(joints)):
            if (active_joint_indices is not None
                    and joint_index not in active_joint_indices):
                result.append([{"mode": 1}] * 3)
                continue
            scalar_start = (
                starts.get(joint_index)
                if starts is not None else group_start + 3 * joint_index
            )
            if scalar_start is None:
                result.append([{"mode": 1}] * 3)
                continue
            tracks = source_decoded["tracks"][scalar_start:scalar_start + 3]
            tracks.extend({"mode": 1} for _ in range(3 - len(tracks)))
            if any(track["mode"] not in range(8) for track in tracks):
                raise UnsupportedMotion("Unsupported experimental rotation mode")
            result.append(tracks)
        return result

    tracks_by_joint = collect_tracks(decoded, scalar_starts)
    reference_tracks_by_joint = (
        collect_tracks(
            reference_decoded,
            reference_scalar_starts or scalar_starts,
        ) if reference_decoded else None
    )

    adaptive_units = {}
    if angle_units == "adaptive":
        for joint_index, tracks in enumerate(tracks_by_joint):
            candidates = list(tracks)
            if reference_tracks_by_joint is not None:
                candidates.extend(reference_tracks_by_joint[joint_index])
            maximum = max(
                (abs(value) for track in candidates
                 for value in track.get("values", ())),
                default=0.0,
            )
            adaptive_units[joint_index] = (
                "degrees" if maximum > 2 * math.pi + 0.01 else "radians"
            )

    def source_quaternion(tracks, joint_index, frame):
        units = (
            "radians" if joint_index < 33 else "degrees"
        ) if angle_units == "auto" else (
            adaptive_units[joint_index]
            if angle_units == "adaptive" else angle_units
        )
        angles = [
            _sample_linear(
                track, frame,
                1.0 if track["mode"] == 3 else
                -1.0 if track["mode"] == 4 else 0.0,
            )
            for track in tracks
        ]
        quaternion = (0.0, 0.0, 0.0, 1.0)
        for source_axis, angle in enumerate(angles):
            target_axis = "xyz".index(axis_map[source_axis])
            if axis_signs[source_axis] == "-":
                angle = -angle
            quaternion = multiply(
                axis_quaternion(target_axis, angle, units), quaternion,
            )
        return quaternion

    reference_by_joint = None
    if reference_tracks_by_joint is not None:
        if reference_frame is None:
            reference_frame = reference_decoded["frame_count"] - 1
        reference_by_joint = [
            source_quaternion(tracks, joint_index, reference_frame)
            for joint_index, tracks in enumerate(reference_tracks_by_joint)
        ]

    driver_by_joint = {}
    deforming = set(deforming_joint_indices or ())
    if deforming and reference_joints and scalar_starts is None:
        def reference_parent(index):
            return reference_joints[index].get("parent_index")

        def same_translation(a, b, tolerance=0.02):
            return max(abs(x - y) for x, y in zip(
                reference_joints[a]["translation"],
                reference_joints[b]["translation"],
            )) <= tolerance

        for driver in range(len(joints)):
            if driver in deforming or not any(
                track["mode"] in (5, 6, 7) for track in tracks_by_joint[driver]
            ):
                continue
            candidates = [
                target for target in deforming
                if target != driver
                and reference_parent(target) == reference_parent(driver)
                and same_translation(target, driver)
                and abs(sum(
                    a * b for a, b in zip(
                        global_bind[target], global_bind[driver],
                    )
                )) >= 0.98
            ]
            if len(candidates) == 1:
                driver_by_joint[candidates[0]] = driver

    global_cache = {}

    def global_rotation(joint_index, frame):
        key = (joint_index, frame)
        if key in global_cache:
            return global_cache[key]
        track_joint = driver_by_joint.get(joint_index, joint_index)
        tracks = tracks_by_joint[track_joint]
        # Modes 1 and 2 are implicit zero offsets in the observed rigs.
        if all(track["mode"] in (1, 2) for track in tracks):
            quaternion = global_bind[joint_index]
        else:
            source_rotation = source_quaternion(tracks, joint_index, frame)
            source_delta = source_rotation
            if reference_by_joint is not None:
                # Interpret stored orientations around a known neutral pose.
                # This cancels a constant per-joint basis while preserving all
                # relative motion in the curve.
                source_delta = multiply(
                    inverse(reference_by_joint[track_joint]), source_delta,
                )
            if rotation_model.startswith("global_reference_"):
                reference_rotation = reference_by_joint[track_joint]
                if rotation_model == "global_reference_active":
                    delta = multiply(
                        source_rotation, inverse(reference_rotation),
                    )
                    quaternion = multiply(delta, global_bind[joint_index])
                elif rotation_model == "global_reference_active_inverse":
                    delta = multiply(
                        reference_rotation, inverse(source_rotation),
                    )
                    quaternion = multiply(delta, global_bind[joint_index])
                elif rotation_model == "global_reference_row":
                    delta = multiply(
                        inverse(reference_rotation), source_rotation,
                    )
                    quaternion = multiply(global_bind[joint_index], delta)
                else:
                    delta = multiply(
                        inverse(source_rotation), reference_rotation,
                    )
                    quaternion = multiply(global_bind[joint_index], delta)
            elif rotation_model in (
                "local_absolute", "local_delta_post", "local_delta_pre",
            ):
                parent = joints[joint_index].get("parent")
                bind_local = (
                    global_bind[joint_index] if parent is None else
                    multiply(inverse(global_bind[parent]), global_bind[joint_index])
                )
                quaternion = (
                    source_delta if rotation_model == "local_absolute" else
                    multiply(bind_local, source_delta)
                    if rotation_model == "local_delta_post" else
                    multiply(source_delta, bind_local)
                )
                if parent is not None:
                    quaternion = multiply(
                        global_rotation(parent, frame), quaternion,
                    )
            elif rotation_model == "global_delta_active":
                quaternion = multiply(source_delta, global_bind[joint_index])
            elif rotation_model == "global_delta_row":
                quaternion = multiply(global_bind[joint_index], source_delta)
            elif rotation_model == "global_delta_row_inverse":
                quaternion = multiply(
                    global_bind[joint_index], inverse(source_delta),
                )
            else:
                delta = (-source_delta[0], -source_delta[1],
                         -source_delta[2], source_delta[3])
                # Apply the source-row delta in global bind space, then make
                # the resulting global rotation local to the animated parent.
                quaternion = multiply(delta, global_bind[joint_index])
        norm = math.sqrt(sum(value * value for value in quaternion))
        quaternion = tuple(value / norm for value in quaternion)
        global_cache[key] = quaternion
        return quaternion

    channels = []
    frames = list(range(decoded["frame_count"]))
    for joint_index in joint_indices:
        values = []
        previous = None
        parent = joints[joint_index].get("parent")
        for frame in frames:
            quaternion = global_rotation(joint_index, frame)
            if parent is not None:
                quaternion = multiply(
                    inverse(global_rotation(parent, frame)), quaternion,
                )
            if previous is not None and sum(
                a * b for a, b in zip(previous, quaternion)
            ) < 0:
                quaternion = tuple(-value for value in quaternion)
            values.append(quaternion)
            previous = quaternion
        channels.append({
            "joint": joint_index, "path": "rotation",
            "times": [frame / fps for frame in frames], "values": values,
            "interpolation": "LINEAR", "experimental": True,
            "source_representation": (
                f"{rotation_model} XYZ Euler {angle_units}, converted to local"
            ),
            "source_axis_map": axis_map,
            "source_axis_signs": axis_signs,
            "rotation_group_start": group_start,
            "source_scalar_indices": (
                list(range(
                    scalar_starts[driver_by_joint.get(joint_index, joint_index)],
                    scalar_starts[driver_by_joint.get(joint_index, joint_index)] + 3,
                )) if scalar_starts is not None else list(range(
                    group_start + 3 * driver_by_joint.get(joint_index, joint_index),
                    group_start + 3 * driver_by_joint.get(joint_index, joint_index) + 3,
                ))
            ),
            "controller_joint": driver_by_joint.get(joint_index),
            "rotation_reference_frame": (
                reference_frame if reference_by_joint is not None else None
            ),
        })
    return channels


def infer_humanoid_joint_scalar_starts(skeleton, canonical_scalar_count):
    """Bind the observed humanoid transform-list segments to skeleton joints.

    chr301/302/303 share an exact segment layout. Within a segment, one
    transform triplet is stored for each skeleton joint in skeleton order,
    followed by a fixed number of auxiliary rig-control triplets. Accessory
    joints remain in skeleton order; chr301 adds six accessory controls.
    """
    joints = skeleton.get("joints", [])
    bone_ids = [joint.get("global_id") for joint in joints]
    if len(joints) < 42 or canonical_scalar_count < 8:
        raise UnsupportedMotion("Skeleton is not a supported humanoid layout")
    if (canonical_scalar_count - 8) % 3:
        raise UnsupportedMotion("Humanoid transform list is not triplet-aligned")

    left_arm = [10, 11, 35, 13, 37, 15, 251, 36, 34]
    right_arm = [40, 41, 65, 43, 67, 45, 250, 66, 64]
    left_leg = [200, 206, 201, 208, 202, 203, 209, 207, 204]
    right_leg = [214, 210, 216, 211, 218, 212, 213, 219, 217]

    def find_subsequence(values, start=0):
        for index in range(start, len(bone_ids) - len(values) + 1):
            if bone_ids[index:index + len(values)] == values:
                return index
        raise UnsupportedMotion(
            f"Humanoid skeleton segment is missing: {values}"
        )

    if bone_ids[:5] != [0, 1, 2, 101, 110]:
        raise UnsupportedMotion("Unsupported humanoid root/spine order")
    left_arm_start = find_subsequence(left_arm, 5)
    right_arm_start = find_subsequence(right_arm, left_arm_start + 9)
    pelvis_start = find_subsequence([4], right_arm_start + 9)
    left_leg_start = find_subsequence(left_leg, pelvis_start + 1)
    right_leg_start = find_subsequence(right_leg, left_leg_start + 9)
    if right_leg_start + 9 != len(joints):
        raise UnsupportedMotion("Unsupported joints after humanoid leg segments")

    triplet_count = (canonical_scalar_count - 8) // 3
    auxiliary_count = triplet_count - (len(joints) - 1)
    fixed_auxiliary_count = 3 + 3 + 3 + 1 + 7 + 7
    accessory_auxiliary_count = auxiliary_count - fixed_auxiliary_count
    if accessory_auxiliary_count < 0:
        raise UnsupportedMotion("Humanoid transform list lacks rig controls")

    scalar_starts = {}
    group = 0

    def bind(indices):
        nonlocal group
        for joint_index in indices:
            scalar_starts[joint_index] = 8 + 3 * group
            group += 1

    bind(range(1, 5))
    group += 3
    bind(range(5, left_arm_start))
    group += accessory_auxiliary_count
    bind(range(left_arm_start, left_arm_start + 9))
    group += 3
    bind(range(right_arm_start, right_arm_start + 9))
    group += 3
    bind(range(right_arm_start + 9, pelvis_start))
    bind([pelvis_start])
    group += 1
    bind(range(left_leg_start, left_leg_start + 9))
    group += 7
    bind(range(right_leg_start, right_leg_start + 9))
    group += 7
    if group != triplet_count:
        raise UnsupportedMotion(
            f"Humanoid transform binding consumed {group}/{triplet_count} triplets"
        )
    return scalar_starts, {
        "root_scalar_count": 8,
        "triplet_count": triplet_count,
        "joint_triplet_count": len(scalar_starts),
        "auxiliary_triplet_count": auxiliary_count,
        "accessory_auxiliary_triplet_count": accessory_auxiliary_count,
    }


def apply_experimental_humanoid_ik(decoded, skeleton, channels, scalar_starts,
                                   fps=30.0, reference_decoded=None,
                                   reference_scalar_starts=None,
                                   reference_frame=None,
                                   export_control_channels=False,
                                   bake_ik=True,
                                   target_orientation_mode="source-row"):
    """Bake or export observed humanoid model-space effectors.

    Cross-character comparison identifies four position triplets shared by
    chr301/302/303.  They are model-space wrist/ankle targets, not Euler
    rotations.  In bake mode, existing FK curves provide the bend plane and
    this pass changes only the upper and lower rotations needed to reach each
    target.  In export mode, FK is left untouched and controls are emitted for
    a runtime solver.  The optional orientation interpretation converts the
    adjacent source row-vector Euler basis by quaternion inversion, matching
    the established HSC instance-to-Godot convention.
    """
    joints = skeleton.get("joints", [])
    by_id = {joint.get("global_id"): joint["index"] for joint in joints}
    required = (11, 13, 15, 41, 43, 45, 200, 201, 202, 210, 211, 212,
                251, 66, 204, 217)
    if any(bone_id not in by_id for bone_id in required):
        raise UnsupportedMotion("Humanoid IK bones/controllers are missing")

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

    def normalize(q):
        length = math.sqrt(sum(value * value for value in q))
        return tuple(value / length for value in q)

    def rotate(q, v):
        vector = (v[0], v[1], v[2], 0.0)
        return multiply(multiply(q, vector), inverse(q))[:3]

    def add(a, b):
        return tuple(x + y for x, y in zip(a, b))

    def subtract(a, b):
        return tuple(x - y for x, y in zip(a, b))

    def scale(v, factor):
        return tuple(value * factor for value in v)

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def length(v):
        return math.sqrt(dot(v, v))

    def unit(v, fallback=(1.0, 0.0, 0.0)):
        magnitude = length(v)
        return scale(v, 1.0 / magnitude) if magnitude > 1e-8 else fallback

    def cross(a, b):
        return (
            a[1]*b[2] - a[2]*b[1],
            a[2]*b[0] - a[0]*b[2],
            a[0]*b[1] - a[1]*b[0],
        )

    def from_to(source, target):
        a, b = unit(source), unit(target)
        cosine = max(-1.0, min(1.0, dot(a, b)))
        if cosine > 1.0 - 1e-8:
            return (0.0, 0.0, 0.0, 1.0)
        if cosine < -1.0 + 1e-8:
            axis = cross(a, (1.0, 0.0, 0.0))
            if length(axis) < 1e-6:
                axis = cross(a, (0.0, 1.0, 0.0))
            axis = unit(axis)
            return (axis[0], axis[1], axis[2], 0.0)
        axis = cross(a, b)
        return normalize((axis[0], axis[1], axis[2], 1.0 + cosine))

    frame_count = decoded["frame_count"]
    rotation_channels = {
        channel["joint"]: channel for channel in channels
        if channel["path"] == "rotation"
    }
    translation_channels = {
        channel["joint"]: channel for channel in channels
        if channel["path"] == "translation"
    }

    def sample_channel(channel, frame, fallback):
        if channel is None:
            return fallback
        times, values = channel["times"], channel["values"]
        time = frame / fps
        for right in range(1, len(times)):
            if time <= times[right]:
                left = right - 1
                span = times[right] - times[left]
                alpha = 0.0 if span == 0 else (time - times[left]) / span
                value = tuple(
                    a * (1.0 - alpha) + b * alpha
                    for a, b in zip(values[left], values[right])
                )
                return normalize(value)
        return values[-1]

    def sample_translation(channel, frame, fallback):
        if channel is None:
            return fallback
        times, values = channel["times"], channel["values"]
        time = frame / fps
        for right in range(1, len(times)):
            if time <= times[right]:
                left = right - 1
                span = times[right] - times[left]
                alpha = 0.0 if span == 0 else (time - times[left]) / span
                return tuple(
                    a * (1.0 - alpha) + b * alpha
                    for a, b in zip(values[left], values[right])
                )
        return values[-1]

    local_frames = [
        [sample_channel(
            rotation_channels.get(index), frame, tuple(joint["rotation"]),
        ) for frame in range(frame_count)]
        for index, joint in enumerate(joints)
    ]

    if target_orientation_mode not in ("source-row", "none"):
        raise ValueError(
            f"Unknown IK target orientation mode: {target_orientation_mode}"
        )
    target_specs = [
        ((11, 13, 15), scalar_starts[by_id[251]] + 2,
         scalar_starts[by_id[251]] + 5),
        ((41, 43, 45), scalar_starts[by_id[66]] + 2,
         scalar_starts[by_id[66]] + 5),
        ((200, 201, 202), scalar_starts[by_id[204]] + 2,
         scalar_starts[by_id[204]] + 5),
        ((210, 211, 212), scalar_starts[by_id[217]] + 4,
         scalar_starts[by_id[217]] + 7),
    ]
    if any(
        (orientation_start if target_orientation_mode == "source-row"
         else target_start) + 2 >= decoded["scalar_count"]
        for chain, target_start, orientation_start in target_specs
    ):
        raise UnsupportedMotion("Humanoid IK target is truncated")

    def sample_target(start, frame):
        return tuple(_sample_linear(
            track, frame,
            1.0 if track["mode"] == 3 else
            -1.0 if track["mode"] == 4 else 0.0,
        ) for track in decoded["tracks"][start:start + 3])

    def source_row_orientation(source_decoded, start, frame):
        angles = tuple(
            _sample_linear(track, frame, 0.0)
            for track in source_decoded["tracks"][start:start + 3]
        )
        source = (0.0, 0.0, 0.0, 1.0)
        for axis, angle in enumerate(angles):
            half = angle * 0.5
            axis_rotation = [0.0, 0.0, 0.0, math.cos(half)]
            axis_rotation[axis] = math.sin(half)
            source = multiply(tuple(axis_rotation), source)
        # Source transforms multiply row vectors. Converting the same basis to
        # glTF/Godot column-vector convention transposes it, i.e. inverts its
        # unit quaternion. This is the same correction as HSC instances.
        return normalize(inverse(source))

    def forward(frame):
        positions, rotations = [], []
        for index, joint in enumerate(joints):
            parent = joint.get("parent")
            local_rotation = local_frames[index][frame]
            translation = tuple(joint["translation"])
            if parent is None:
                positions.append(sample_translation(
                    translation_channels.get(index), frame, translation,
                ))
                rotations.append(local_rotation)
            else:
                positions.append(add(
                    positions[parent], rotate(rotations[parent], translation),
                ))
                rotations.append(multiply(rotations[parent], local_rotation))
        return positions, rotations

    bind_positions, bind_rotations = [], []
    for index, joint in enumerate(joints):
        parent = joint.get("parent")
        local_rotation = tuple(joint["rotation"])
        translation = tuple(joint["translation"])
        if parent is None:
            bind_positions.append(translation)
            bind_rotations.append(local_rotation)
        else:
            bind_positions.append(add(
                bind_positions[parent],
                rotate(bind_rotations[parent], translation),
            ))
            bind_rotations.append(multiply(
                bind_rotations[parent], local_rotation,
            ))

    reference_orientations = {}
    if (target_orientation_mode == "source-row"
            and reference_decoded is not None
            and reference_scalar_starts is not None):
        if reference_frame is None:
            reference_frame = reference_decoded["frame_count"] - 1
        reference_specs = {
            15: reference_scalar_starts[by_id[251]] + 5,
            45: reference_scalar_starts[by_id[66]] + 5,
            202: reference_scalar_starts[by_id[204]] + 5,
            212: reference_scalar_starts[by_id[217]] + 7,
        }
        reference_orientations = {
            by_id[bone_id]: source_row_orientation(
                reference_decoded, start, reference_frame,
            )
            for bone_id, start in reference_specs.items()
        }

    def desired_orientation(end, orientation_start, frame):
        orientation = source_row_orientation(
            decoded, orientation_start, frame,
        )
        if end in reference_orientations:
            orientation = multiply(
                orientation, inverse(reference_orientations[end]),
            )
            orientation = multiply(orientation, bind_rotations[end])
        return normalize(orientation)

    solved_joints = set()
    if bake_ik:
        for frame in range(frame_count):
            for bone_ids, target_start, orientation_start in target_specs:
                upper, middle, end = (
                    by_id[bone_id] for bone_id in bone_ids
                )
                positions, rotations = forward(frame)
                origin = positions[upper]
                provisional_middle = positions[middle]
                target = sample_target(target_start, frame)
                first_length = length(tuple(joints[middle]["translation"]))
                second_length = length(tuple(joints[end]["translation"]))
                direction_vector = subtract(target, origin)
                distance = max(1e-6, length(direction_vector))
                direction = unit(direction_vector)
                clamped = min(
                    max(distance, abs(first_length - second_length) + 1e-5),
                    first_length + second_length - 1e-5,
                )
                along = (
                    clamped*clamped + first_length*first_length
                    - second_length*second_length
                ) / (2.0 * clamped)
                height = math.sqrt(max(
                    0.0, first_length*first_length - along*along,
                ))
                pole = subtract(provisional_middle, origin)
                if bone_ids[0] in (200, 210):
                    parent = joints[upper].get("parent")
                    bind_pole = subtract(
                        bind_positions[middle], bind_positions[upper],
                    )
                    if parent is not None:
                        bind_pole = rotate(
                            inverse(bind_rotations[parent]), bind_pole,
                        )
                        pole = rotate(rotations[parent], bind_pole)
                pole = subtract(pole, scale(direction, dot(pole, direction)))
                if length(pole) < 1e-5:
                    pole = cross(direction, (0.0, 0.0, 1.0))
                    if length(pole) < 1e-5:
                        pole = cross(direction, (0.0, 1.0, 0.0))
                desired_middle = add(
                    add(origin, scale(direction, along)),
                    scale(unit(pole), height),
                )

                parent = joints[upper].get("parent")
                correction = from_to(
                    subtract(provisional_middle, origin),
                    subtract(desired_middle, origin),
                )
                upper_global = multiply(correction, rotations[upper])
                local_frames[upper][frame] = (
                    upper_global if parent is None else
                    multiply(inverse(rotations[parent]), upper_global)
                )

                positions, rotations = forward(frame)
                correction = from_to(
                    subtract(positions[end], positions[middle]),
                    subtract(target, positions[middle]),
                )
                middle_global = multiply(correction, rotations[middle])
                local_frames[middle][frame] = multiply(
                    inverse(rotations[upper]), middle_global,
                )

                solved_joints.update((upper, middle))
                if target_orientation_mode == "source-row":
                    positions, rotations = forward(frame)
                    local_frames[end][frame] = multiply(
                        inverse(rotations[middle]),
                        desired_orientation(end, orientation_start, frame),
                    )
                    solved_joints.add(end)

    for joint_index in solved_joints:
        channel = rotation_channels.get(joint_index)
        values = local_frames[joint_index]
        previous = None
        for frame, quaternion in enumerate(values):
            quaternion = normalize(quaternion)
            if previous is not None and dot(previous, quaternion) < 0:
                quaternion = tuple(-value for value in quaternion)
            values[frame] = quaternion
            previous = quaternion
        if channel is None:
            channel = {
                "joint": joint_index, "path": "rotation",
                "interpolation": "LINEAR", "experimental": True,
            }
            channels.append(channel)
        channel.update({
            "times": [frame / fps for frame in range(frame_count)],
            "values": values,
            "source_representation": "two-bone IK from model-space effector",
            "ik_baked": True,
        })
    if export_control_channels:
        control_names = (
            "ik_hand_l_target", "ik_hand_r_target",
            "ik_foot_l_target", "ik_foot_r_target",
        )
        for control_name, (bone_ids, target_start, orientation_start) in zip(
            control_names, target_specs,
        ):
            translations = [
                sample_target(target_start, frame)
                for frame in range(frame_count)
            ]
            times = [frame / fps for frame in range(frame_count)]
            channels.append({
                "joint": -1, "ik_control": control_name,
                "path": "translation", "times": times,
                "values": translations, "interpolation": "LINEAR",
                "experimental": True,
                "source_representation": "model-space IK effector position",
            })
            if target_orientation_mode == "source-row":
                end = by_id[bone_ids[2]]
                channels.append({
                    "joint": -1, "ik_control": control_name,
                    "path": "rotation", "times": times,
                    "values": [
                        desired_orientation(end, orientation_start, frame)
                        for frame in range(frame_count)
                    ],
                    "interpolation": "LINEAR", "experimental": True,
                    "source_representation": (
                        "reference-relative source row-vector Euler IK "
                        "orientation converted to glTF/Godot"
                    ),
                })
    return channels


def remap_humanoid_scalar_starts(skeleton, scalar_starts, scalar_count,
                                 canonical_scalar_count):
    """Account for the observed optional two-scalar humanoid sections.

    The four layouts differ by an early two-scalar controller, a second pair
    immediately before the right-leg block, and an unused two-scalar suffix.
    Descriptor indices after an omitted section must be shifted; treating all
    clips as the maximum layout assigns arm/leg curves to the wrong controls.
    """
    difference = canonical_scalar_count - scalar_count
    if difference not in (0, 2, 4, 6):
        raise UnsupportedMotion(
            f"Unsupported humanoid scalar layout difference: {difference}"
        )
    by_id = {
        joint.get("global_id"): joint.get("index", index)
        for index, joint in enumerate(skeleton.get("joints", []))
    }
    right_leg_start = scalar_starts[by_id[214]]
    omit_early = difference in (2, 6)
    omit_before_right_leg = difference in (4, 6)
    remapped = {}
    for joint_index, canonical_start in scalar_starts.items():
        shift = 0
        if omit_early and canonical_start >= 26:
            shift += 2
        if omit_before_right_leg and canonical_start >= right_leg_start:
            shift += 2
        remapped[joint_index] = canonical_start - shift
    return remapped


def infer_rotation_group_start(motion: bytes, boundaries, joint_count):
    """Reject the former contiguous joint-suffix interpretation.

    Cross-rig comparison shows that the scalar stream contains ordered
    transform triplets for both skeleton joints and auxiliary rig controls.
    Those controls are interleaved with joint entries, so subtracting three
    scalars per joint only produces an arithmetical remainder, not a binding
    offset.
    """
    scalar_counts = [
        struct.unpack_from(">H", motion, start)[0]
        for start, end in zip(boundaries, boundaries[1:])
        if end - start >= 2
    ]
    if not scalar_counts:
        raise UnsupportedMotion("No nonempty clips available for layout inference")
    canonical_count = max(scalar_counts)
    if canonical_count >= 8 and (canonical_count - 8) % 3 == 0:
        triplet_count = (canonical_count - 8) // 3
        auxiliary_count = triplet_count - max(0, joint_count - 1)
        detail = (
            f"; observed {triplet_count} transform triplets for "
            f"{max(0, joint_count - 1)} non-root joints"
        )
        if auxiliary_count >= 0:
            detail += f" ({auxiliary_count} auxiliary triplets)"
    else:
        detail = f"; canonical scalar count is {canonical_count}"
    raise UnsupportedMotion(
        "Joint rotations are not a contiguous scalar suffix: rig-control "
        f"triplets are interleaved with joint triplets{detail}"
    )


def decode_experimental_root_translation(decoded, skeleton, fps=30.0):
    """Build the verified chr300 root-position channel (scalar slots 2..4)."""
    if decoded["scalar_count"] < 5 or not skeleton.get("joints"):
        raise UnsupportedMotion("Clip has no root translation layout")
    tracks = decoded["tracks"][2:5]
    if any(track["mode"] not in (1, 5, 6, 7) for track in tracks):
        raise UnsupportedMotion("Unsupported root translation storage mode")
    frames = sorted({
        frame for track in tracks for frame in track.get("frames", [0])
    })
    bind = skeleton["joints"][0]["translation"]
    return {
        "joint": 0,
        "path": "translation",
        "times": [frame / fps for frame in frames],
        "values": [tuple(
            _sample_linear(track, frame, bind[axis])
            for axis, track in enumerate(tracks)
        ) for frame in frames],
        "interpolation": "LINEAR",
    }


def decode_root_yaw(decoded, fps=30.0):
    """Build chr300's root heading channel from scalar slot 0.

    Left/right turn clips establish radians around the Y-up axis by symmetry;
    slot 1 is reserved and remains implicit zero in all 150 observed clips.
    """
    if decoded["scalar_count"] < 2:
        raise UnsupportedMotion("Clip has no root heading layout")
    track = decoded["tracks"][0]
    reserved = decoded["tracks"][1]
    if track["mode"] not in (0, 5, 6, 7) or reserved["mode"] != 0:
        raise UnsupportedMotion("Unsupported root heading storage mode")
    frames = track.get("frames", [0])
    values = []
    previous = None
    for frame in frames:
        angle = _sample_linear(track, frame, 0.0)
        half = angle * 0.5
        quaternion = (0.0, math.sin(half), 0.0, math.cos(half))
        if previous is not None and sum(
            a * b for a, b in zip(previous, quaternion)
        ) < 0:
            quaternion = tuple(-value for value in quaternion)
        values.append(quaternion)
        previous = quaternion
    return {
        "joint": 0,
        "path": "rotation",
        "times": [frame / fps for frame in frames],
        "values": values,
        "interpolation": "LINEAR",
        "source_scalar_index": 0,
        "source_representation": "absolute Y-axis heading in radians",
    }


def decode_root_rotation(decoded, skeleton, fps=30.0, source="combined"):
    """Decode root rotation, optionally excluding the duplicated heading."""
    if decoded["scalar_count"] < 8 or not skeleton.get("joints"):
        raise UnsupportedMotion("Clip has no complete root rotation layout")
    heading = decoded["tracks"][0]
    reserved = decoded["tracks"][1]
    local = decoded["tracks"][5:8]
    if heading["mode"] not in (0, 5, 6, 7) or reserved["mode"] != 0:
        raise UnsupportedMotion("Unsupported extracted heading storage mode")
    if any(track["mode"] not in (0, 1, 5, 6, 7) for track in local):
        raise UnsupportedMotion("Unsupported local root rotation storage mode")
    if source not in ("combined", "local", "heading"):
        raise ValueError(f"Unknown root rotation source: {source}")

    def multiply(a, b):
        x1, y1, z1, w1 = a
        x2, y2, z2, w2 = b
        return (
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2,
            w1*w2 - x1*x2 - y1*y2 - z1*z2,
        )

    frames = sorted({
        frame for track in (heading, *local)
        for frame in track.get("frames", [0])
    })
    values = []
    previous = None
    for frame in frames:
        angles = [_sample_linear(track, frame, 0.0) for track in local]
        x, y, z = (angle * 0.5 for angle in angles)
        qx = (math.sin(x), 0.0, 0.0, math.cos(x))
        qy = (0.0, math.sin(y), 0.0, math.cos(y))
        qz = (0.0, 0.0, math.sin(z), math.cos(z))
        local_rotation = multiply(qz, multiply(qy, qx))
        heading_angle = _sample_linear(heading, frame, 0.0) * 0.5
        extracted_heading = (
            0.0, math.sin(heading_angle), 0.0, math.cos(heading_angle),
        )
        quaternion = (
            multiply(extracted_heading, local_rotation)
            if source == "combined" else
            local_rotation if source == "local" else extracted_heading
        )
        if previous is not None and sum(
            a * b for a, b in zip(previous, quaternion)
        ) < 0:
            quaternion = tuple(-value for value in quaternion)
        values.append(quaternion)
        previous = quaternion
    return {
        "joint": 0,
        "path": "rotation",
        "times": [frame / fps for frame in frames],
        "values": values,
        "interpolation": "LINEAR",
        "source_scalar_indices": [0, 5, 6, 7],
        "source_representation": (
            f"{source} root rotation from extracted heading and local XYZ Euler radians"
        ),
    }


def decode_character_animations(motion_info, skeleton, clip_indices=None,
                                experimental_root_motion=False,
                                experimental_rotation_joints=None,
                                experimental_rotation_units="radians",
                                deforming_joint_indices=None,
                                experimental_rotation_axes="xyz",
                                experimental_rotation_signs="+++",
                                experimental_rotation_model="local_delta_post",
                                experimental_rotation_reference_clip=None,
                                experimental_rotation_reference_frame="end",
                                experimental_root_rotation_source="combined",
                                experimental_deforming_rotations_only=False,
                                experimental_humanoid_ik=False,
                                experimental_export_ik_targets=False,
                                experimental_ik_target_orientation="source-row"):
    """Inspect selected clips; do not export guessed scalar-to-joint bindings.

    Scalar storage has been verified separately from rig semantics. Returning
    deformed animation channels here would falsely claim complete support.
    Keep the limitation machine-readable in the converter's analysis report.
    """
    if not motion_info or not motion_info.get("animation_boundaries"):
        return []
    motion = Path(motion_info["sequence_path"]).read_bytes()
    reference = None
    if motion_info.get("skeleton_segment_offset") is not None:
        start = motion_info["skeleton_segment_offset"]
        end = motion_info["first_clip_offset"]
        reference = decode_motion_skeleton(motion[start:end])
        target_joints = skeleton.get("joints", [])
        if target_joints:
            target_ids = [joint["global_id"] for joint in target_joints]
            if target_ids != reference["bone_ids"]:
                raise UnsupportedMotion(
                    "Motion reference skeleton order does not match the DDM skeleton"
                )
            max_translation_error = max(
                abs(a - b)
                for target, source in zip(target_joints, reference["joints"])
                for a, b in zip(target["translation"], source["translation"])
            )
            motion_info["reference_pose_translation_max_error"] = max_translation_error
            motion_info["reference_pose_validated"] = max_translation_error < 1e-4
    boundaries = motion_info["animation_boundaries"]
    rotation_group_start = None
    rotation_scalar_starts = None
    canonical_scalar_count = None
    if experimental_rotation_joints:
        scalar_counts = [
            struct.unpack_from(">H", motion, start)[0]
            for start, end in zip(boundaries, boundaries[1:])
            if end - start >= 2
        ]
        canonical_scalar_count = max(scalar_counts)
        rotation_scalar_starts, layout = infer_humanoid_joint_scalar_starts(
            skeleton, canonical_scalar_count,
        )
        motion_info["humanoid_transform_layout"] = layout
    rotation_reference = None
    rotation_reference_frame = None
    rotation_reference_scalar_starts = None
    if experimental_rotation_reference_clip is not None:
        index = experimental_rotation_reference_clip
        if not 0 <= index < len(boundaries) - 1:
            raise ValueError(f"Rotation reference clip index {index} is out of range")
        if boundaries[index] == boundaries[index + 1]:
            raise ValueError(f"Rotation reference clip {index} is empty")
        rotation_reference = decode_scalar_clip(
            motion[boundaries[index]:boundaries[index + 1]]
        )
        rotation_reference_frame = (
            0 if experimental_rotation_reference_frame == "start" else
            rotation_reference["frame_count"] - 1
        )
        if rotation_scalar_starts is not None:
            rotation_reference_scalar_starts = remap_humanoid_scalar_starts(
                skeleton, rotation_scalar_starts,
                rotation_reference["scalar_count"], canonical_scalar_count,
            )
        motion_info["experimental_rotation_reference"] = {
            "clip_index": index,
            "frame": rotation_reference_frame,
            "endpoint": experimental_rotation_reference_frame,
        }
    selected = list(dict.fromkeys(clip_indices)) if clip_indices is not None else list(
        range(min(3, len(boundaries) - 1))
    )
    diagnostics = []
    animations = []
    for index in selected:
        if not 0 <= index < len(boundaries) - 1:
            raise ValueError(f"Motion clip index {index} is out of range")
        original_names = motion_info.get("clip_name_slots", [])
        original_name = original_names[index] if index < len(original_names) else None
        entry = {"name": original_name or f"motion_{index:03d}",
                 "source_clip_index": index,
                 "rig_binding_decoded": False}
        if boundaries[index] == boundaries[index + 1]:
            entry.update(empty_clip_slot=True, scalar_storage_decoded=False)
            diagnostics.append(entry)
            continue
        try:
            decoded = decode_scalar_clip(motion[boundaries[index]:boundaries[index + 1]])
            entry.update({key: value for key, value in decoded.items() if key != "tracks"})
            entry["scalar_storage_decoded"] = True
            modes = Counter(track["mode"] for track in decoded["tracks"])
            entry["mode_counts"] = {
                str(mode): modes.get(mode, 0) for mode in range(8)
            }
            entry["stored_scalar_count"] = sum(
                track["payload_size"] > 0 for track in decoded["tracks"]
            )
            entry["animated_scalar_count"] = sum(
                track["mode"] in (6, 7) for track in decoded["tracks"]
            )
            entry["keyframe_count"] = sum(
                len(track.get("frames", ())) for track in decoded["tracks"]
                if track["mode"] in (6, 7)
            )
            if skeleton.get("joints") and decoded["scalar_count"] >= 8:
                root_rotation = decode_root_rotation(
                    decoded, skeleton, source=experimental_root_rotation_source,
                )
                root_translation = decode_experimental_root_translation(
                    decoded, skeleton,
                )
                entry["root_transform"] = {
                    "rotation_initial": root_rotation["values"][0],
                    "rotation_final": root_rotation["values"][-1],
                    "translation_initial": root_translation["values"][0],
                    "translation_final": root_translation["values"][-1],
                    "rotation_dot_initial_final": sum(
                        a * b for a, b in zip(
                            root_rotation["values"][0],
                            root_rotation["values"][-1],
                        )
                    ),
                }
            if experimental_root_motion:
                channels = [
                    decode_experimental_root_translation(decoded, skeleton),
                    decode_root_rotation(
                        decoded, skeleton,
                        source=experimental_root_rotation_source,
                    ),
                ]
                if experimental_rotation_joints:
                    clip_rotation_scalar_starts = remap_humanoid_scalar_starts(
                        skeleton, rotation_scalar_starts,
                        decoded["scalar_count"], canonical_scalar_count,
                    )
                    selected_rotation_joints = [
                        joint for joint in experimental_rotation_joints
                        if joint != 0 and (
                            not experimental_deforming_rotations_only
                            or joint in set(deforming_joint_indices or ())
                        )
                    ]
                    channels.extend(decode_experimental_joint_rotations(
                        decoded, skeleton, selected_rotation_joints,
                        angle_units=experimental_rotation_units,
                        group_start=rotation_group_start,
                        scalar_starts=clip_rotation_scalar_starts,
                        reference_skeleton=reference,
                        deforming_joint_indices=deforming_joint_indices,
                        axis_map=experimental_rotation_axes,
                        axis_signs=experimental_rotation_signs,
                        rotation_model=experimental_rotation_model,
                        reference_decoded=rotation_reference,
                        reference_frame=rotation_reference_frame,
                        reference_scalar_starts=rotation_reference_scalar_starts,
                        active_joint_indices=(
                            set(selected_rotation_joints)
                            if experimental_deforming_rotations_only else None
                        ),
                    ))
                    if experimental_humanoid_ik or experimental_export_ik_targets:
                        apply_experimental_humanoid_ik(
                            decoded, skeleton, channels,
                            clip_rotation_scalar_starts,
                            reference_decoded=rotation_reference,
                            reference_scalar_starts=rotation_reference_scalar_starts,
                            reference_frame=rotation_reference_frame,
                            export_control_channels=experimental_export_ik_targets,
                            bake_ik=experimental_humanoid_ik,
                            target_orientation_mode=(
                                experimental_ik_target_orientation
                            ),
                        )
                animations.append({
                    "name": entry["name"] + "_experimental",
                    "channels": channels,
                    "source_clip_index": index,
                })
                entry["root_translation_exported"] = True
                entry["experimental_rotation_joints"] = list(
                    experimental_rotation_joints or []
                )
                entry["experimental_humanoid_ik"] = experimental_humanoid_ik
                entry["experimental_ik_mode"] = (
                    "bake" if experimental_humanoid_ik else
                    "godot" if experimental_export_ik_targets else "none"
                )
                entry["experimental_ik_target_orientation"] = (
                    experimental_ik_target_orientation
                )
        except UnsupportedMotion as exc:
            entry.update(scalar_storage_decoded=False, error=str(exc))
        diagnostics.append(entry)
    limitation = (
        "Root translation and Y-axis heading are verified. Structurally bound humanoid "
        "joint rotations use experimental XYZ Euler deltas in local bind space."
        if animations else
        "Scalar-to-joint binding and transform conventions are unresolved; no guessed channels exported."
    )
    motion_info.update(decoded=bool(animations), decoded_clip_count=len(animations),
                       inspected_clips=diagnostics,
                       decoding_limitation=limitation)
    return animations


def inspect_motion_clips(motion_info, clip_indices):
    """Return complete scalar tracks for focused reverse-engineering work."""
    boundaries = motion_info.get("animation_boundaries") if motion_info else None
    if not boundaries:
        raise UnsupportedMotion("No bounded animation clips were discovered")
    motion = Path(motion_info["sequence_path"]).read_bytes()
    inspected = []
    for index in dict.fromkeys(clip_indices):
        if not 0 <= index < len(boundaries) - 1:
            raise ValueError(f"Motion clip index {index} is out of range")
        names = motion_info.get("clip_name_slots", [])
        if boundaries[index] == boundaries[index + 1]:
            inspected.append({
                "name": (names[index] if index < len(names) and names[index]
                         else f"motion_{index:03d}"),
                "source_clip_index": index,
                "source_offset": boundaries[index],
                "source_size": 0,
                "empty_clip_slot": True,
            })
            continue
        decoded = decode_scalar_clip(
            motion[boundaries[index]:boundaries[index + 1]]
        )
        decoded.update(
            name=(names[index] if index < len(names) and names[index]
                  else f"motion_{index:03d}"),
            source_clip_index=index,
            source_offset=boundaries[index],
            source_size=boundaries[index + 1] - boundaries[index],
        )
        inspected.append(decoded)
    return inspected


def compare_scalar_layout(motion_info, clip_indices=None, group_start=5,
                          group_width=4):
    """Compare descriptor positions across clips without assigning semantics.

    Descriptor tables are prefix-coded: clips with fewer descriptors omit a
    suffix that remains at its rig default.  The comparison therefore aligns
    tracks by scalar index and reports absent suffix entries explicitly rather
    than mistaking 247/249/251/253-entry chr300 clips for different layouts.
    Magnitudes are included as evidence only; they do not decide whether a
    slot is a rotation, translation, scale, or constraint parameter.
    """
    boundaries = motion_info.get("animation_boundaries") if motion_info else None
    if not boundaries:
        raise UnsupportedMotion("No bounded animation clips were discovered")
    indices = (list(range(len(boundaries) - 1)) if clip_indices is None
               else list(dict.fromkeys(clip_indices)))
    clips = inspect_motion_clips(motion_info, indices)
    decoded_clips = [clip for clip in clips if not clip.get("empty_clip_slot")]
    if not decoded_clips:
        raise UnsupportedMotion("Selected clip slots are all empty")
    canonical_count = max(clip["scalar_count"] for clip in decoded_clips)
    slots = []
    for scalar_index in range(canonical_count):
        modes = Counter()
        values = []
        present = []
        for clip in decoded_clips:
            if scalar_index >= clip["scalar_count"]:
                continue
            track = clip["tracks"][scalar_index]
            modes[track["mode"]] += 1
            values.extend(track.get("values", ()))
            present.append(clip["source_clip_index"])
        slot = {
            "scalar_index": scalar_index,
            "present_clip_count": len(present),
            "implicit_suffix_clip_count": len(decoded_clips) - len(present),
            "mode_counts": {str(mode): count for mode, count in sorted(modes.items())},
            "stored_value_count": len(values),
        }
        if values:
            slot.update(value_min=min(values), value_max=max(values),
                        max_abs=max(abs(value) for value in values))
        if scalar_index >= group_start:
            relative = scalar_index - group_start
            slot.update(candidate_group=relative // group_width,
                        candidate_component=relative % group_width)
        slots.append(slot)
    return {
        "clip_indices": indices,
        "clip_count": len(clips),
        "decoded_clip_count": len(decoded_clips),
        "empty_clip_count": len(clips) - len(decoded_clips),
        "scalar_counts": dict(sorted(Counter(
            clip["scalar_count"] for clip in decoded_clips
        ).items())),
        "canonical_scalar_count": canonical_count,
        "candidate_group_start": group_start,
        "candidate_group_width": group_width,
        "clips": [{
            "source_clip_index": clip["source_clip_index"],
            "name": clip["name"],
            "frame_count": clip.get("frame_count"),
            "scalar_count": clip.get("scalar_count"),
            "empty_clip_slot": clip.get("empty_clip_slot", False),
        } for clip in clips],
        "slots": slots,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Inspect character motionSequence/motionPackage resources.",
    )
    parser.add_argument("model", type=Path, help="Path to the character DDM")
    parser.add_argument(
        "--clips", default="0,1,2",
        help="Comma-separated zero-based clips to inspect (default: 0,1,2)",
    )
    parser.add_argument(
        "--dump-tracks", type=Path,
        help="Write complete decoded scalar tracks for the selected clips as JSON",
    )
    parser.add_argument(
        "--dump-layout", type=Path,
        help=("Compare scalar positions across the selected clips and write a "
              "compact JSON layout report"),
    )
    parser.add_argument(
        "--all-clips", action="store_true",
        help="Use every bounded clip for --dump-layout instead of --clips",
    )
    args = parser.parse_args()
    info = discover_character_motion(args.model)
    if info is None:
        raise SystemExit("No motion resources found for this model")
    # Inspection deliberately does not require a decoded DDM skeleton: the
    # scalar storage layer is independent from rig binding.
    clips = [int(value.strip()) for value in args.clips.split(",") if value.strip()]
    decode_character_animations(info, {"joints": []}, clips)
    if args.dump_tracks:
        args.dump_tracks.parent.mkdir(parents=True, exist_ok=True)
        args.dump_tracks.write_text(
            json.dumps(inspect_motion_clips(info, clips), indent=2),
            encoding="utf-8",
        )
    if args.dump_layout:
        args.dump_layout.parent.mkdir(parents=True, exist_ok=True)
        compared = compare_scalar_layout(info, None if args.all_clips else clips)
        args.dump_layout.write_text(
            json.dumps(compared, indent=2), encoding="utf-8",
        )
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
