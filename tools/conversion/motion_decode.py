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
                                        fps=30.0, angle_units="degrees"):
    """Decode selected chr300 rotation triples as absolute local Euler angles.

    The curve table has five leading scalars followed by four scalars per
    reference-skeleton joint. The first three values behave as XYZ angles in
    degrees or radians; the fourth value is deliberately ignored until its
    transform meaning is established. XYZ angles are composed extrinsically
    and hemisphere-corrected before they reach glTF.
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

    if angle_units not in ("degrees", "radians", "auto"):
        raise ValueError(f"Unknown angle units: {angle_units}")

    def axis_quaternion(axis, angle, units):
        half = (math.radians(angle) if units == "degrees" else angle) * 0.5
        result = [0.0, 0.0, 0.0, math.cos(half)]
        result[axis] = math.sin(half)
        return tuple(result)

    def quaternion_to_xyz(quaternion):
        """Return extrinsic XYZ angles for q = qz * qy * qx."""
        x, y, z, w = quaternion
        return (
            math.atan2(2 * (w*x + y*z), 1 - 2 * (x*x + y*y)),
            math.asin(max(-1.0, min(1.0, 2 * (w*y - z*x)))),
            math.atan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z)),
        )

    joints = skeleton.get("joints", [])
    expected = 5 + 4 * len(joints)
    if decoded["scalar_count"] != expected:
        raise UnsupportedMotion(
            f"Experimental rotation layout requires {expected} scalars, "
            f"got {decoded['scalar_count']}"
        )
    channels = []
    for joint_index in joint_indices:
        if not 0 <= joint_index < len(joints):
            raise UnsupportedMotion(f"Rotation joint {joint_index} is out of range")
        group = decoded["tracks"][5 + 4 * joint_index:9 + 4 * joint_index]
        tracks = group[:3]
        units = (
            "radians" if joint_index < 33 else "degrees"
        ) if angle_units == "auto" else angle_units
        if any(track["mode"] not in (0, 1, 3, 4, 5, 6, 7) for track in tracks):
            raise UnsupportedMotion("Unsupported experimental rotation mode")
        frames = sorted({
            frame for track in tracks for frame in track.get("frames", [0])
        })
        bind_angles = quaternion_to_xyz(joints[joint_index]["rotation"])
        if units == "degrees":
            bind_angles = tuple(math.degrees(value) for value in bind_angles)
        values = []
        previous = None
        for frame in frames:
            if all(track["mode"] in (0, 1) for track in tracks):
                quaternion = tuple(joints[joint_index]["rotation"])
                values.append(quaternion)
                previous = quaternion
                continue
            angles = [
                _sample_linear(
                    track, frame,
                    1.0 if track["mode"] == 3 else
                    -1.0 if track["mode"] == 4 else bind_angles[axis],
                )
                for axis, track in enumerate(tracks)
            ]
            quaternion = (0.0, 0.0, 0.0, 1.0)
            for axis, angle in enumerate(angles):
                quaternion = multiply(axis_quaternion(axis, angle, units), quaternion)
            norm = math.sqrt(sum(value * value for value in quaternion))
            quaternion = tuple(value / norm for value in quaternion)
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
            "source_representation": f"absolute extrinsic XYZ Euler {units}",
            "ignored_scalar_index": 5 + 4 * joint_index + 3,
        })
    return channels


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


def decode_root_rotation(decoded, skeleton, fps=30.0):
    """Compose extracted heading with chr300 joint-0 local Euler rotation."""
    if decoded["scalar_count"] < 8 or not skeleton.get("joints"):
        raise UnsupportedMotion("Clip has no complete root rotation layout")
    heading = decoded["tracks"][0]
    reserved = decoded["tracks"][1]
    local = decoded["tracks"][5:8]
    if heading["mode"] not in (0, 5, 6, 7) or reserved["mode"] != 0:
        raise UnsupportedMotion("Unsupported extracted heading storage mode")
    if any(track["mode"] not in (0, 1, 5, 6, 7) for track in local):
        raise UnsupportedMotion("Unsupported local root rotation storage mode")

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
        quaternion = multiply(extracted_heading, local_rotation)
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
            "extracted Y heading composed with absolute local XYZ Euler radians"
        ),
    }


def decode_character_animations(motion_info, skeleton, clip_indices=None,
                                experimental_root_motion=False,
                                experimental_rotation_joints=None,
                                experimental_rotation_units="degrees"):
    """Inspect selected clips; do not export guessed scalar-to-joint bindings.

    Scalar storage has been verified separately from rig semantics. Returning
    deformed animation channels here would falsely claim complete support.
    Keep the limitation machine-readable in the converter's analysis report.
    """
    if not motion_info or not motion_info.get("animation_boundaries"):
        return []
    motion = Path(motion_info["sequence_path"]).read_bytes()
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
                root_rotation = decode_root_rotation(decoded, skeleton)
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
                    decode_root_rotation(decoded, skeleton),
                ]
                if experimental_rotation_joints:
                    channels.extend(decode_experimental_joint_rotations(
                        decoded, skeleton, [joint for joint in experimental_rotation_joints
                                            if joint != 0],
                        angle_units=experimental_rotation_units,
                    ))
                animations.append({
                    "name": entry["name"] + "_experimental",
                    "channels": channels,
                    "source_clip_index": index,
                })
                entry["root_translation_exported"] = True
                entry["experimental_rotation_joints"] = list(
                    experimental_rotation_joints or []
                )
        except UnsupportedMotion as exc:
            entry.update(scalar_storage_decoded=False, error=str(exc))
        diagnostics.append(entry)
    limitation = (
        "Root translation and Y-axis heading are verified. Selected joint rotations are an "
        "experimental absolute XYZ Euler interpretation; each fourth scalar is ignored."
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
        decoded = decode_scalar_clip(
            motion[boundaries[index]:boundaries[index + 1]]
        )
        names = motion_info.get("clip_name_slots", [])
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
    canonical_count = max(clip["scalar_count"] for clip in clips)
    slots = []
    for scalar_index in range(canonical_count):
        modes = Counter()
        values = []
        present = []
        for clip in clips:
            if scalar_index >= clip["scalar_count"]:
                continue
            track = clip["tracks"][scalar_index]
            modes[track["mode"]] += 1
            values.extend(track.get("values", ()))
            present.append(clip["source_clip_index"])
        slot = {
            "scalar_index": scalar_index,
            "present_clip_count": len(present),
            "implicit_suffix_clip_count": len(clips) - len(present),
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
        "scalar_counts": dict(sorted(Counter(
            clip["scalar_count"] for clip in clips
        ).items())),
        "canonical_scalar_count": canonical_count,
        "candidate_group_start": group_start,
        "candidate_group_width": group_width,
        "clips": [{
            "source_clip_index": clip["source_clip_index"],
            "name": clip["name"],
            "frame_count": clip["frame_count"],
            "scalar_count": clip["scalar_count"],
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
