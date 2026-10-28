"""Observed SSR parameters and an explicitly approximate cloth-grid export.

Reflection-verified values are retained verbatim. The Godot XPBD graph is a
conversion, not the undecoded original TaskGxClothBase solver.
"""
from __future__ import annotations
import hashlib
import math
from pathlib import Path
import struct
try:
    from .glb_export import _joint_global_matrices
    from .softbody_collision import decode_ssr_collision_masks
    from .player_armor import is_player_asset
except ImportError:
    from glb_export import _joint_global_matrices
    from softbody_collision import decode_ssr_collision_masks
    from player_armor import is_player_asset

def extract(model: Path, resource: Path) -> dict:
    try:
        from .ddm.skinned import decode_skinned_skeleton
    except ImportError:
        from ddm.skinned import decode_skinned_skeleton
    data = resource.read_bytes()
    groups = decode_ssr_collision_masks(data)
    chain_count, cloth_count = struct.unpack_from(">2I", data, 0x80)
    skeleton = decode_skinned_skeleton(model.read_bytes())
    joints = skeleton["joints"]
    by_id = {j["global_id"]: i for i, j in enumerate(joints)}
    bind_matrices = _joint_global_matrices(joints, 0.01)

    def position(bone):
        matrix = bind_matrices[by_id[bone]]
        return [matrix[3], matrix[7], matrix[11]]

    def rest_geometry(columns):
        # Measurements, not a claim about the original solver's spring graph.
        def distance(a, b):
            return dict(bones=[a, b], distance_m=math.dist(position(a), position(b)))

        return dict(scale=0.01, units="export metres",
            bone_positions=[dict(bone=bone, position_m=position(bone))
                            for column in columns for bone in column],
            root_parent_bones=[joints[joints[by_id[column[0]]]["parent"]]["global_id"]
                               if joints[by_id[column[0]]]["parent"] is not None else None
                               for column in columns],
            along_column=[distance(a, b) for column in columns
                          for a, b in zip(column, column[1:])],
            between_adjacent_columns=[distance(a, b)
                for left, right in zip(columns, columns[1:]) for a, b in zip(left, right)])

    def floats(offset, count):
        values = list(struct.unpack_from(f">{count}f", data, offset))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Non-finite SSR parameters")
        return values

    def integers(offset, count):
        return list(struct.unpack_from(f">{count}I", data, offset))

    def bone_chain(tail, count):
        result = []
        index = by_id[tail]
        for _ in range(count):
            if index is None:
                raise ValueError("SSR bone chain exceeds source skeleton hierarchy")
            result.append(joints[index]["global_id"])
            index = joints[index]["parent"]
        return list(reversed(result))

    chains = []
    cursor = 0x90 + chain_count * 0x60 + cloth_count * 0x190
    expected = cursor + chain_count * 0x1E0 + cloth_count * 0xC10 + 0x10
    if len(data) != expected:
        raise ValueError("Unsupported SSR additional-data packing")
    for i in range(chain_count):
        offset = 0x90 + i * 0x60
        count = integers(offset + 8, 1)[0]
        tail = groups[i]["tail_bones"][0]
        kt, kb, mass, damping, delta = floats(offset + 0xC, 5)
        chains.append(dict(groups[i], header_offset=offset, joint_count=count,
            bones=bone_chain(tail, count), kt=kt, kb=kb, mass=mass,
            air_damping=damping, update_delta=delta,
            simulation_enabled=bool(integers(offset + 0x20, 1)[0]),
            use_motion_gravity=bool(integers(offset + 0x28, 1)[0]),
            move_strength_raw=integers(offset + 0x2C, 2),
            tail_offset=floats(offset + 0x40, 3),
            use_additional_data=bool(integers(offset + 0x50, 1)[0]),
            twist_interpolate_speed=floats(offset + 0x54, 1)[0],
            additional=dict(offset=cursor, radius=floats(cursor + 8, 16),
                normals=[floats(cursor + 0x50 + n * 16, 3) for n in range(16)],
                twist_limit=floats(cursor + 0x150, 16),
                angle_limit=floats(cursor + 0x190, 16))))
        cursor += 0x1E0

    cloths = []
    for i in range(cloth_count):
        offset = 0x90 + chain_count * 0x60 + i * 0x190
        strands, count = integers(offset + 8, 2)
        group = groups[chain_count + i]
        columns = [bone_chain(tail, count) for tail in group["tail_bones"]]
        kt, ks, kb, mass, damping, delta = floats(offset + 0x10, 6)
        cloths.append(dict(group, header_offset=offset, strand_count=strands,
            bones_per_strand=count,
            bone_columns=columns, derived_rest_geometry=rest_geometry(columns),
            kt=kt, ks=ks, kb=kb, mass=mass, air_damping=damping,
            update_delta=delta, simulation_enabled=bool(integers(offset + 0x28, 1)[0]),
            use_motion_gravity=bool(integers(offset + 0x30, 1)[0]),
            move_strength_raw=integers(offset + 0x34, 2),
            tail_offsets=[floats(offset + 0x80 + n * 16, 3) for n in range(strands)],
            use_additional_data=bool(integers(offset + 0x180, 1)[0]),
            additional=dict(offset=cursor, capacity=256,
                radius=floats(cursor + 8, 256),
                twist_limit=floats(cursor + 0x408, 256),
                angle_limit=floats(cursor + 0x808, 256))))
        cursor += 0xC10
    return dict(resource=str(resource), sha256=hashlib.sha256(data).hexdigest(),
        model=str(model), model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        chains=chains, cloths=cloths,
        limitations=["stiffness coefficients are source values, not Godot parameter equivalents",
                     "additional table entries include inactive capacity; cell indexing must be traced",
                     "cross-strand spring graph, fixed cells and solver equations need runtime-code verification",
                     "bone rest lengths and widths can be derived from DDM, not explicit CSR distance constraints"])


def player_cloth_surfaces(model_path, skeleton, chains, collision_profile, scale):
    model_path = Path(model_path)
    if not is_player_asset(model_path.stem):
        return []
    folders = [model_path.parent, model_path.parent.parent / "chr100"]
    resource = None
    for folder in dict.fromkeys(folders):
        for path in sorted(folder.glob(folder.name + ".*")):
            with path.open("rb") as stream:
                if stream.read(4) == b"\0SSR":
                    resource = path
                    break
        if resource is not None:
            break
    if resource is None:
        return []
    source = extract(resource.parent / resource.parent.name, resource)
    available = {c["end_bone"] for c in chains}
    names = {j["global_id"]: j["name"] for j in skeleton["joints"]}
    surfaces = []
    for cloth in source["cloths"]:
        columns = cloth["bone_columns"]
        # Do not simulate invisible or incomplete cloth panels on armor variants.
        if any(b not in names for column in columns for b in column):
            continue
        if not all(names[column[-1]] in available for column in columns):
            continue
        surfaces.append(dict(name=cloth["group"],
            bone_columns=[[names[b] for b in column] for column in columns],
            tail_offsets=[[v * scale for v in t] for t in cloth["tail_offsets"]],
            kt=cloth["kt"], ks=cloth["ks"], kb=cloth["kb"], mass=cloth["mass"],
            air_damping=cloth["air_damping"], update_delta=cloth["update_delta"],
            # Until cell indexing is verified, use the largest source radius
            # conservatively, rather than misassigning angular/radius slots.
            particle_radius=max(cloth["additional"]["radius"]) * scale,
            collision_names=[c["name"] for c in collision_profile["collisions"]
                if c["source_record"]["check_flag"] & cloth["check_flag"]],
            source_parameters=cloth, source_file=str(resource),
            source_sha256=source["sha256"],
            solver="xpbd_grid_approximation", source_scale=scale,
            stiffness_conversion="relative_strain_piecewise_v1",
            unmapped_fields=["twist_limit", "angle_limit", "move_strength_raw"],
            max_strain=0.02))
    return surfaces
