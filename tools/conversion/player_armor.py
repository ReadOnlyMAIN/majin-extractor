"""Player mesh partition inferred from the shared chr1xx bone hierarchy.

The original equipment table is not decoded. Assign whole triangles by summed
skin influence: head below bone 101, lower body below bone 4, torso otherwise.
No vertex is moved or duplicated across slot boundaries.
"""
from collections import defaultdict
import re


def is_player_asset(name: str) -> bool:
    return re.fullmatch(r"chr1\d{2}", name) is not None


def darkness_material_indices(materials):
    """Observed player corruption surfaces; keep these separate from equipment."""
    return {m['index'] for m in materials
            if re.fullmatch(r'(?:chr1\d{2})?(?:t_body|t_huku|t_manto|tbody|tar_.*)',
                            m['name'].casefold())}


def partition_player_parts(vertices, parts, skeleton, materials=()):
    slots = []
    for joint in skeleton['joints']:
        current = joint
        seen = set()
        slot = 'torso'
        while current['index'] not in seen:
            seen.add(current['index'])
            if current['global_id'] in (101, 4):
                slot = 'head' if current['global_id'] == 101 else 'legs'
                break
            if current['parent'] is None:
                break
            current = skeleton['joints'][current['parent']]
        slots.append(slot)
    darkness = darkness_material_indices(materials)
    result = []
    for part in parts:
        groups = defaultdict(list)
        for triangle in part['triangles']:
            influence = dict.fromkeys(('torso', 'legs', 'head'), 0.0)
            for index in triangle:
                vertex = vertices[index]
                for joint, weight in zip(vertex['joints'], vertex['weights']):
                    influence[slots[joint]] += weight
            groups[max(influence, key=influence.get)].append(triangle)
        for slot, triangles in groups.items():
            suffix = '_darkness' if part.get('material_index') in darkness else ''
            result.append(dict(part, armor_slot=slot + suffix, triangles=triangles))
    return result
