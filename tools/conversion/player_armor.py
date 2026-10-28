"""Player render groups and legacy bone-influence partition helpers.

Runtime costumes use whole source bodies. Only the legacy partition helper
splits head/torso/legs; it is retained for existing callers and old exports.
"""
from collections import defaultdict
import re


def is_player_asset(name: str) -> bool:
    return re.fullmatch(r"chr1\d{2}", name) is not None


def darkness_material_indices(materials):
    """Observed player corruption surfaces; keep these separate from equipment."""
    return {m['index'] for m in materials
            if (int((m.get('render_state') or {}).get('shader_key') or '0', 16) & 0x00200000)
            or re.fullmatch(r'(?:chr1\d{2})?(?:t_body|t_huku|t_manto|tbody|tar_.*)',
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


def is_player_accessory(name: str) -> bool:
    return re.fullmatch(r"chr7[24]\d", name) is not None


def partition_player_render_parts(parts, materials, vertices=None, bracelets=False, skeleton=None):
    """Preserve source surfaces, exposing bracers and authored hair chains."""
    darkness = darkness_material_indices(materials)
    hair_bones = {2, 101, 110, 190, 191, 192, 193, 194}
    vertex_bones = [
        {skeleton['joints'][joint]['global_id']
         for joint, weight in zip(vertex['joints'], vertex['weights']) if weight > 0}
        for vertex in vertices
    ] if skeleton is not None and vertices is not None else None

    def hair_slot(bones):
        if bones <= hair_bones:
            if bones & {193, 194}:
                return 'hair_palm'
            if bones & {191, 192}:
                return 'hair_fringe'
        return 'body'

    result = []
    for part in parts:
        suffix = "_darkness" if part.get("material_index") in darkness else ""
        if bracelets:
            groups = defaultdict(list)
            for triangle in part['triangles']:
                side = "positive" if sum(vertices[i]['position'][0] for i in triangle) >= 0 else "negative"
                groups[side].append(triangle)
            for side, triangles in groups.items():
                result.append(dict(part, armor_slot="bracelet_" + side + suffix, triangles=triangles))
        else:
            slot = "body"
            if vertex_bones is not None:
                bones = set().union(*(vertex_bones[index] for triangle in part['triangles'] for index in triangle))
                # These are the authored ponytail/fringe chains, not a spatial
                # cutoff: face and scalp surfaces retain their original geometry.
                slot = hair_slot(bones)
                if slot == 'body' and bones & {191, 192, 193, 194}:
                    # Some tar surfaces combine hair and face/torso. Preserve
                    # their facial and body triangles when hiding the hair.
                    groups = defaultdict(list)
                    for triangle in part['triangles']:
                        groups[hair_slot(set().union(*(vertex_bones[index] for index in triangle)))].append(triangle)
                    for group, triangles in groups.items():
                        result.append(dict(part, armor_slot=group + suffix, triangles=triangles))
                    continue
            result.append(dict(part, armor_slot=slot + suffix))
    return result


def apply_player_material_policy(materials, asset_name):
    """Do not treat an unconfirmed legacy triplet as a textured player tint.

    KbBaseP28 shader reflection exposes no diffuse-color uniform. The source
    triplet differs between gameplay and cinematic bodies even with identical
    albedo images (chr100/101, chr110/111). Preserve it as evidence, not a tint.
    """
    if not is_player_asset(asset_name):
        return
    for material in materials:
        estimate = material.get('pbr_estimate')
        if estimate and material.get('texture_references'):
            estimate['legacy_diffuse_candidate'] = estimate.get('diffuse')
            estimate['diffuse'] = [1.0, 1.0, 1.0]
            estimate['diffuse_source'] = 'player_texture_rgb_without_unverified_legacy_tint'
