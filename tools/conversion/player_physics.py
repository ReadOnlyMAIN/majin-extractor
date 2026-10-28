"""Conservative secondary-motion profile for the chr1 body skeletons.

These IDs are cloth/hair chains verified against DDM skin influences, not a
rule that treats every unanimated bone (including face/IK controls) as cloth.
Parameters are Godot preview defaults, not recovered original simulation data.
"""
from __future__ import annotations

try:
    from .player_armor import is_player_asset
except ImportError:
    from player_armor import is_player_asset


PLAYER_SPRING_CHAINS = (
    ("hair_fringe", (190, 191, 192)),
    ("hair_palm", (193, 194)),
    ("cape_center", (5, 6, 7, 8, 9, 12, 39, 44)),
    ("cape_left", (79, 85, 102, 103, 104, 105, 107, 109)),
    ("cape_right", (113, 115, 117, 189, 220, 221, 223, 225)),
    ("cloth_back", (86, 87, 88, 89, 90)),
    ("cloth_side", (91, 92, 93, 94, 95)),
    ("cloth_front", (96, 97, 98, 99, 100)),
    ("cloth_short_back", (70, 71, 72)),
    ("cloth_short_side", (73, 74)),
    ("cloth_short_front", (75, 76, 77, 78)),
    ("cloth_leg_back", (227, 228)),
    ("cloth_leg_side", (229, 230)),
    ("cloth_leg_front", (231, 232)),
)


def player_spring_chains(asset_name, skeleton, vertices, mesh_parts, scale=1.0):
    """Export only intact known chains that influence rendered geometry."""
    if not is_player_asset(asset_name):
        return []
    joints = skeleton["joints"]
    by_id = {joint["global_id"]: index for index, joint in enumerate(joints)}
    used = {
        joint_index
        for vertex_index in {i for part in mesh_parts for tri in part["triangles"] for i in tri}
        for joint_index, weight in zip(vertices[vertex_index]["joints"],
                                       vertices[vertex_index]["weights"])
        if weight > 0
    }
    result = []
    for name, bone_ids in PLAYER_SPRING_CHAINS:
        if any(bone not in by_id for bone in bone_ids):
            continue
        indices = [by_id[bone] for bone in bone_ids]
        if any(joints[child]["parent"] != parent
               for parent, child in zip(indices, indices[1:])):
            continue
        if not used.intersection(indices):
            continue
        hair = name.startswith("hair_")
        # DDM chain segments point along local +X. Extend the last bone so
        # weighted leaf joints also rotate; length is in exported model units.
        tail_length = sum(v * v for v in joints[indices[-1]]["translation"]) ** 0.5
        stiffness = 1.5 if hair else 0.8
        drag = 0.4 if hair else 0.6
        result.append({
            "name": name,
            "root_bone": joints[indices[0]]["name"],
            "end_bone": joints[indices[-1]]["name"],
            "end_length": max(tail_length, 1.0) * scale,
            "stiffness": stiffness,
            "drag": drag,
            "radius": 2.0 * scale,
            # Native spring gravity is a preview bias, not SI acceleration.
            # Moving the former external_force (0,-1,0) here preserves motion.
            "gravity": 1.0,
            "gravity_mode": "per_joint",
            "joint_settings": [dict(bone=joints[index]["name"],
                stiffness=stiffness, drag=drag, radius=2.0 * scale, gravity=1.0,
                gravity_direction=[0.0, -1.0, 0.0]) for index in indices],
        })
    return result
