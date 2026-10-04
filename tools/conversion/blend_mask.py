"""Compute material blend maps and weights for smooth submesh transitions.



The original game assigns one material per submesh, producing hard transitions
at submesh boundaries. This module reconstructs the smooth transitions by
measuring, for every vertex, how close it is to faces belonging to other
materials, then baking those weights into a UV-space texture that the Godot
shader samples to interpolate between material texture sets.

The blend map is a 2D texture indexed by the vertex UV. Each texel stores up to
four material-slot weights packed into RGBA. Because the DDM stores UVs
directly (no atlas packing), the same UV can be shared by vertices on different
submeshes; in that case the weights are averaged. Texels that no vertex maps to
receive a neutral (owner-only) weight.

``build_blend_map`` also returns the slot -> material index table so the caller
can bind each ``blend_base_texture_N`` uniform to the correct neighbouring
material's diffuse texture. We bake one map per owner material
(:func:`select_material_slots` reserves slot 0 for that owner) because each
owner may be surrounded by a different set of neighbours.

This is an approximation: the original RSX shader may use a mask texture or
derivative-based stencil that is not recoverable from the exported geometry
alone.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections import defaultdict

# Maximum number of distinct materials blended per vertex. Four matches the
# number of sampler slots a single Godot ShaderMaterial can realistically bind.
MAX_BLEND_MATERIALS = 4

# Radius, in metres, over which a neighbouring material influences a vertex.
# The DDM units are centimetre-like and the exporter scales by 0.01, so 0.05
# equals roughly five source units. Small enough to keep large flat surfaces
# single-material, large enough to cover narrow suture bands.
DEFAULT_BLEND_RADIUS = 0.05

# Blend map resolution. The DDM UVs are in [0, 1] and the source textures are
# typically 256 or 512 px, so 256 is plenty for smooth gradients at seams.
BLEND_MAP_SIZE = 256


def _triangle_centroids(vertices, mesh_parts):
    """Yield (material_index, centroid) for every source face."""
    for part in mesh_parts:
        material = part['material_index']
        for triangle in part['triangles']:
            points = [vertices[index]['position'] for index in triangle]
            centroid = tuple(
                sum(point[axis] for point in points) / 3.0
                for axis in range(3)
            )
            yield material, centroid


def _build_material_grid(mesh_parts, vertices, cell):
    """Bucket face centroids into a uniform grid for fast neighbour lookup."""
    grid = defaultdict(list)
    for material, centroid in _triangle_centroids(vertices, mesh_parts):
        key = tuple(int(math.floor(component / cell)) for component in centroid)
        grid[key].append((material, centroid))
    return grid


def _neighbour_centroids(grid, cell, position, radius):
    """Yield (material, centroid) within ``radius`` of ``position``."""
    reach = int(math.ceil(radius / cell))
    base = tuple(int(math.floor(component / cell)) for component in position)
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                bucket = grid.get((base[0] + dx, base[1] + dy, base[2] + dz))
                if not bucket:
                    continue
                for material, centroid in bucket:
                    ddx = centroid[0] - position[0]
                    ddy = centroid[1] - position[1]
                    ddz = centroid[2] - position[2]
                    if ddx*ddx + ddy*ddy + ddz*ddz <= radius * radius:
                        yield material, centroid


def compute_vertex_weights(
    vertices,
    mesh_parts,
    scale,
    radius=DEFAULT_BLEND_RADIUS,
    max_materials=MAX_BLEND_MATERIALS,
    owner_filter=None,
):
    """Return a per-vertex tuple of (material_index, weight) pairs.

    The material a vertex already belongs to always receives at least half the
    total weight so the vertex does not drift towards an unrelated neighbour
    when many materials meet nearby. Vertices with no adjacent foreign material
    receive a single pair equal to their own material with weight 1.0.

    When ``owner_filter`` is set, vertices whose owning material does not match
    are skipped (their entry is ``None``). The neighbour grid still uses every
    ``mesh_parts`` face, so a per-owner blend map can be baked without hiding
    the surrounding materials from the distance query.
    """
    if radius <= 0:
        raise ValueError('Blend radius must be positive.')
    if max_materials < 1:
        raise ValueError('At least one blend material is required.')

    material_for_vertex = [None] * len(vertices)
    for part in mesh_parts:
        for triangle in part['triangles']:
            for index in triangle:
                material_for_vertex[index] = part['material_index']

    cell = radius
    grid = _build_material_grid(mesh_parts, vertices, cell)

    weights = []
    inverse_radius = 1.0 / radius
    for index, vertex in enumerate(vertices):
        position = tuple(value * scale for value in vertex['position'])
        owner = material_for_vertex[index]
        if owner_filter is not None and owner != owner_filter:
            weights.append(None)
            continue
        contributions = defaultdict(float)
        for material, centroid in _neighbour_centroids(grid, cell, position, radius):
            if material == owner:
                continue
            dx = position[0] - centroid[0]
            dy = position[1] - centroid[1]
            dz = position[2] - centroid[2]
            distance = math.sqrt(dx*dx + dy*dy + dz*dz)
            falloff = max(0.0, 1.0 - distance * inverse_radius)
            if falloff > 0.0:
                contributions[material] += falloff
        if not contributions:
            weights.append(((owner, 1.0),))
            continue
        ordered = sorted(contributions.items(), key=lambda item: item[1], reverse=True)
        ordered = ordered[:max_materials - 1]
        neighbour_total = sum(value for _, value in ordered)
        # Owner keeps the majority share; the strongest neighbour reaches 0.5.
        max_neighbour_share = 0.5
        scale_factor = (
            max_neighbour_share / neighbour_total
            if neighbour_total > 0 else 0.0
        )
        descriptor = [(owner, 1.0 - neighbour_total * scale_factor)]
        for material, value in ordered:
            descriptor.append((material, value * scale_factor))
        total = sum(value for _, value in descriptor)
        weights.append(tuple((material, value / total) for material, value in descriptor))
    return weights



def _quantize_channels(values, total=255):
    """Convert normalized RGBA floats into bytes that sum to exactly ``total``.

    Rounding each of the four channels independently can overshoot (e.g. a
    0.5/0.5 split rounds to 128/128, summing to 256). We floor every channel,
    then hand the remaining units to the largest fractional parts so the packed
    bytes always sum to ``total``. An all-zero input returns four zero bytes.
    """
    scaled = [value * total for value in values]
    if sum(scaled) <= 0:
        return bytes(4)
    floors = [int(math.floor(value)) for value in scaled]
    remainder = total - sum(floors)
    if remainder:
        # Order slots by descending fractional part, tie-broken by index.
        order = sorted(
            range(len(scaled)),
            key=lambda index: (scaled[index] - floors[index], -index),
            reverse=True,
        )
        for index in order[:remainder]:
            floors[index] += 1
    return bytes(min(255, max(0, value)) for value in floors)


def write_png(path, width, height, rgba_rows):
    """Write a minimal 8-bit RGBA PNG without external dependencies."""
    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack('>I', len(payload)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    raw = bytearray()
    for row in rgba_rows:
        raw.append(0)  # filter type: none
        raw.extend(row)
    payload = (b'\x89PNG\r\n\x1a\n'
               + chunk(b'IHDR', header)
               + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
               + chunk(b'IEND', b''))
    path.write_bytes(payload)


def build_blend_map(
    vertices,
    mesh_parts,
    material_slots,
    scale,
    radius=DEFAULT_BLEND_RADIUS,
    size=BLEND_MAP_SIZE,
    max_materials=MAX_BLEND_MATERIALS,
    owner_filter=None,
):
    """Bake per-vertex blend weights into a UV-space RGBA texture.

    ``material_slots`` maps a global DDM material index to a local slot 0..3.
    The slot the vertex originally belongs to always lands in the texel's
    dominant channel. Texels that no vertex maps to are left as owner-only for
    the nearest recorded owner; if a texel receives nothing at all, it is
    written as slot 0 with full weight.

    Returns ``(rows, slot_materials)``:
    - ``rows`` is a list of RGBA byte rows, ``size`` rows of ``size * 4`` bytes.
    - ``slot_materials`` is a length-``max_materials`` list mapping each slot to
      the global DDM material index that occupies it (or ``None`` if unassigned).
      The caller uses it to bind each ``blend_base_texture_N`` uniform to the
      correct neighbouring material's diffuse texture.
    """
    if size < 1:
        raise ValueError('Blend map size must be positive.')

    weights = compute_vertex_weights(
        vertices,
        mesh_parts,
        scale,
        radius=radius,
        max_materials=max_materials,
        owner_filter=owner_filter,
    )

    # Reverse map: slot -> material index. Clash-free because ``material_slots``
    # is a bijection between the materials we care about and slots 0..3.
    slot_materials = [None] * max_materials
    for material, slot in material_slots.items():
        if 0 <= slot < max_materials:
            slot_materials[slot] = material

    # Accumulate (slot, weight) pairs per texel.
    accumulators = defaultdict(lambda: defaultdict(float))
    texel_owner = {}
    for vertex, descriptor in zip(vertices, weights):
        if descriptor is None:
            continue
        uv = vertex.get('uv') or (0.0, 0.0)
        x = min(size - 1, max(0, int(uv[0] * size)))
        y = min(size - 1, max(0, int((1.0 - uv[1]) * size)))
        key = (x, y)
        for material, weight in descriptor:
            slot = material_slots.get(material)
            if slot is None:
                continue
            accumulators[key][slot] += weight
        if key not in texel_owner and descriptor:
            owner_material = descriptor[0][0]
            texel_owner[key] = material_slots.get(owner_material, 0)

    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            key = (x, y)
            channels = accumulators.get(key)
            if not channels:
                owner = texel_owner.get(key, 0)
                values = [0.0, 0.0, 0.0, 0.0]
                if 0 <= owner < max_materials:
                    values[owner] = 1.0
                row.extend(_quantize_channels(values))
                continue
            total = sum(channels.values())
            if total <= 0:
                row.extend(bytes((255, 0, 0, 0)))
                continue
            values = [0.0, 0.0, 0.0, 0.0]
            for slot, weight in channels.items():
                if 0 <= slot < max_materials:
                    values[slot] = weight / total
            row.extend(_quantize_channels(values))
        rows.append(row)
    return rows, slot_materials


def select_material_slots(mesh_parts, max_slots=MAX_BLEND_MATERIALS, owner=None):
    """Choose which materials get a blend slot in the generated texture.

    Slot 0 is reserved for ``owner`` when it is provided (the material whose
    .tres binds the blend map). The remaining slots are filled by the other
    materials in order of appearance, up to ``max_slots``. Materials beyond the
    limit are simply not blended by the shader; the nearest owner wins.

    Returns a dict mapping global DDM material index -> slot.
    """
    ordered = []
    seen = set()
    if owner is not None:
        ordered.append(owner)
        seen.add(owner)
    for part in mesh_parts:
        material = part['material_index']
        if material in seen:
            continue
        seen.add(material)
        ordered.append(material)
    return {material: slot for slot, material in enumerate(ordered[:max_slots])}


def pack_weights(weights, material_slots):
    """Pack blend descriptors into shader-friendly index and weight vectors.

    Retained for diagnostic output; the shader path uses :func:`build_blend_map`
    instead.
    """
    indexed = []
    for descriptor in weights:
        slots = []
        scalars = []
        for material, value in descriptor:
            slot = material_slots.get(material)
            if slot is None:
                continue
            slots.append(slot)
            scalars.append(value)
        if not slots:
            raise ValueError('Blend descriptor has no material present in the map.')
        while len(slots) < 4:
            slots.append(slots[0])
            scalars.append(0.0)
        total = sum(scalars)
        if total <= 0:
            raise ValueError('Blend descriptor has no positive weight.')
        indexed.append((
            tuple(slots[:4]),
            tuple(value / total for value in scalars[:4]),
        ))
    return indexed
