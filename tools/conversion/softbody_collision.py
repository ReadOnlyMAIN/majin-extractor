"""Read original CSR soft-body collision volumes, without fitting the mesh.

The 0x70-byte record layout and shape dispatch are verified against the local
PS3 HitSphere/HitCapsule/HitCapsule2 constructors; see research/PLAYER_COLLISIONS.md.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct

try:
    from .glb_export import _joint_global_matrices
    from .player_armor import is_player_asset
except ImportError:
    from glb_export import _joint_global_matrices
    from player_armor import is_player_asset


def decode_csr(data: bytes) -> list[dict]:
    """Decode the observed version-2 CSR layout; reject unknown layouts/shapes."""
    if len(data) < 0xA0 or data[:4] != b"\0CSR" or struct.unpack_from(">I", data, 4)[0] != 2:
        raise ValueError("Unsupported or truncated CSR header")
    count = struct.unpack_from(">I", data, 0x80)[0]
    if len(data) != 0xA0 + count * 0x70:
        raise ValueError("CSR record count/size mismatch")
    records = []
    for index in range(count):
        offset = 0x90 + index * 0x70
        kind, flags, bone_a, bone_b = struct.unpack_from(">4I", data, offset + 8)
        if kind not in (1, 2, 3):
            raise ValueError(f"Unsupported CSR shape {kind} at record {index}")
        point_a = struct.unpack_from(">3f", data, offset + 0x20)
        point_b = struct.unpack_from(">3f", data, offset + 0x30)
        radius_a, radius_b = struct.unpack_from(">2f", data, offset + 0x40)
        values = (*point_a, *point_b, radius_a, radius_b)
        if not all(math.isfinite(v) for v in values) or radius_a <= 0 or (kind == 3 and radius_b <= 0):
            raise ValueError(f"Invalid CSR dimensions at record {index}")
        records.append(dict(index=index, offset=offset, kind=kind, flags=flags,
            bone_a=bone_a, bone_b=bone_b, point_a=list(point_a), point_b=list(point_b),
            radius=radius_a, end_radius=radius_b if kind == 3 else radius_a,
            check_flag=struct.unpack_from(">I", data, offset + 0x64)[0]))
    return records


def decode_ssr_collision_masks(data: bytes) -> list[dict]:
    """Read collision query masks from the observed SSR chain/cloth headers.

    Additional simulation payloads are deliberately not interpreted here.
    These masks are selectors, not a classification of every CSR as body skin.
    """
    if len(data) < 0x90 or data[:4] != b"\0SSR" or struct.unpack_from(">I", data, 4)[0] != 2:
        raise ValueError("Unsupported or truncated SSR header")
    chain_count, cloth_count = struct.unpack_from(">2I", data, 0x80)
    end = 0x90 + chain_count * 0x60 + cloth_count * 0x190
    if end > len(data):
        raise ValueError("SSR simulation headers exceed resource size")
    groups = []
    for index in range(chain_count):
        offset = 0x90 + index * 0x60
        groups.append(dict(group=f"chain_{index}",
            tail_bones=[struct.unpack_from(">I", data, offset + 0x34)[0]],
            check_flag=struct.unpack_from(">I", data, offset + 0x5C)[0]))
    for index in range(cloth_count):
        offset = 0x90 + chain_count * 0x60 + index * 0x190
        strands = struct.unpack_from(">I", data, offset + 8)[0]
        if not 1 <= strands <= 3:
            raise ValueError("Unsupported SSR cloth strand layout")
        groups.append(dict(group=f"cloth_{index}",
            tail_bones=list(struct.unpack_from(f">{strands}I", data, offset + 0x3C)),
            check_flag=struct.unpack_from(">I", data, offset + 0x18C)[0]))
    return groups


def apply_collision_filters(chains, skeleton, profile):
    """Use original SSR group/CSR volume mask intersections for each chain."""
    by_name = {joint["name"]: i for i, joint in enumerate(skeleton["joints"])}
    joints = skeleton["joints"]
    result = []
    for chain in chains:
        members = set()
        index = by_name[chain["end_bone"]]
        root = by_name[chain["root_bone"]]
        while index is not None:
            members.add(joints[index]["global_id"])
            if index == root:
                break
            index = joints[index]["parent"]
        groups = [group for group in profile.get("groups", [])
                  if members.intersection(group["tail_bones"])]
        if len(groups) > 1:
            raise ValueError(f"Ambiguous SSR group for {chain['name']}")
        mask = groups[0]["check_flag"] if groups else 0
        names = [entry["name"] for entry in profile["collisions"]
                 if entry["source_record"]["check_flag"] & mask]
        result.append(dict(chain, collision_mask=mask,
                           collision_group=groups[0]["group"] if groups else None,
                           collision_names=names))
    return result


def map_csr_collisions(records, source_skeleton, target_skeleton, scale):
    """Map source bone-local points through bind space to the target body rig.

    Capsules keep BOTH endpoint bones. Godot's constant-radius capsule uses
    the smaller original radius for HitCapsule2, following the chosen preview
    conversion. It does not reproduce the wider end of the original shape. Sphere records use only point A, as the original factory does.
    """
    source = source_skeleton["joints"]
    target = target_skeleton["joints"]
    source_ids = {j["global_id"]: i for i, j in enumerate(source)}
    target_ids = {j["global_id"]: i for i, j in enumerate(target)}
    source_globals = _joint_global_matrices(source, scale)
    target_globals = _joint_global_matrices(target, scale)

    def map_point(bone, point):
        if bone not in source_ids or bone not in target_ids:
            raise ValueError(f"CSR references missing bone {bone}")
        a = source_globals[source_ids[bone]]
        b = target_globals[target_ids[bone]]
        world = [sum(a[row * 4 + k] * point[k] * scale for k in range(3))
                 + a[row * 4 + 3] for row in range(3)]
        # Rigid inverse: R^T * (world - translation).
        return [sum(b[row * 4 + col] * (world[row] - b[row * 4 + 3])
                    for row in range(3)) for col in range(3)]

    result = []
    for record in records:
        if record["flags"] == 0:
            continue
        bone_a = record["bone_a"]
        entry = dict(name=f"CSR_{record['index']:02d}",
            shape="sphere" if record["kind"] == 1 else "capsule",
            bone_a=f"bone_{bone_a:03d}", point_a=map_point(bone_a, record["point_a"]),
            radius=min(record["radius"], record["end_radius"]) * scale,
            source_record=record)
        if record["kind"] != 1:
            bone_b = record["bone_b"]
            entry.update(bone_b=f"bone_{bone_b:03d}",
                         point_b=map_point(bone_b, record["point_b"]))
        result.append(entry)
    return result


def player_collision_profile(model_path, skeleton, scale=1.0):
    """Use a body's own CSR, or chr100's shared player collision resource."""
    model_path = Path(model_path)
    if not is_player_asset(model_path.stem):
        return {"collisions": [], "groups": [], "source": None}
    folders = [model_path.parent]
    master = model_path.parent.parent / "chr100"
    if master not in folders:
        folders.append(master)
    for folder in folders:
        for path in sorted(folder.glob(folder.name + ".*")):
            with path.open("rb") as stream:
                if stream.read(4) != b"\0CSR":
                    continue
            try:
                from .ddm.skinned import decode_skinned_skeleton
            except ImportError:
                from ddm.skinned import decode_skinned_skeleton
            data = path.read_bytes()
            source_skeleton = decode_skinned_skeleton((folder / folder.name).read_bytes())
            groups = []
            ssr_source = None
            for simulation in sorted(folder.glob(folder.name + ".*")):
                with simulation.open("rb") as stream:
                    if stream.read(4) != b"\0SSR":
                        continue
                ssr_data = simulation.read_bytes()
                groups = decode_ssr_collision_masks(ssr_data)
                ssr_source = {"file": f"{folder.name}/{simulation.name}",
                              "sha256": hashlib.sha256(ssr_data).hexdigest()}
                break
            return {"collisions": map_csr_collisions(decode_csr(data), source_skeleton, skeleton, scale),
                    "groups": groups,
                    "source": {"file": f"{folder.name}/{path.name}",
                               "sha256": hashlib.sha256(data).hexdigest(),
                               "simulation": ssr_source,
                               "groups": groups,
                               "shared_player_profile": folder != model_path.parent,
                               "capsule2_conversion": "constant capsule with minimum source radius"}}
    return {"collisions": [], "groups": [], "source": None}
