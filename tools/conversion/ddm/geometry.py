"""Mesh topology, submesh descriptors and map section decoding."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable

from .binary import (
    ATTRIBUTE_STRIDE, POSITION_STRIDE, be_u32, decode_u16_buffer,
    score_position_stream, vec_cross, vec_dot, vec_len, vec_normalize, vec_sub,
)


SUBMESH_SIGNATURE = struct.pack(">II", 1, 4)


def triangle_list(indices: list[int], vertex_count: int):
    tris = []
    rejected = 0
    degenerate = 0

    usable = len(indices) - (len(indices) % 3)

    for i in range(0, usable, 3):
        a, b, c = indices[i:i+3]
        if a >= vertex_count or b >= vertex_count or c >= vertex_count:
            rejected += 1
            continue
        if a == b or b == c or a == c:
            degenerate += 1
            continue
        tris.append((a, b, c))

    return tris, {
        "input_indices": len(indices),
        "usable_indices": usable,
        "triangles": len(tris),
        "rejected_triangles": rejected,
        "degenerate_triangles": degenerate,
    }


def triangle_strip(indices: list[int], vertex_count: int):
    tris = []
    rejected = 0
    degenerate = 0
    parity = 0

    for i in range(len(indices) - 2):
        a, b, c = indices[i], indices[i+1], indices[i+2]

        if a >= vertex_count or b >= vertex_count or c >= vertex_count:
            rejected += 1
            parity = 0
            continue

        if a == b or b == c or a == c:
            degenerate += 1
            # Degenerate strip triangles are intentionally retained only
            # as separators, not exported.
            parity ^= 1
            continue

        if parity:
            a, b = b, a

        tris.append((a, b, c))
        parity ^= 1

    return tris, {
        "input_indices": len(indices),
        "triangles": len(tris),
        "rejected_windows": rejected,
        "degenerate_windows": degenerate,
    }


def topology_stats(vertices, triangles):
    used = set()
    area_nonzero = 0
    total_area2 = 0.0

    pts = [v["position"] for v in vertices]

    for a, b, c in triangles:
        if max(a, b, c) >= len(pts):
            continue
        used.update((a, b, c))
        ab = vec_sub(pts[b], pts[a])
        ac = vec_sub(pts[c], pts[a])
        area2 = vec_len(vec_cross(ab, ac))
        total_area2 += area2
        if area2 > 1e-8:
            area_nonzero += 1

    return {
        "used_vertices": len(used),
        "coverage_ratio": len(used) / len(vertices) if vertices else 0.0,
        "nonzero_area_triangles": area_nonzero,
        "total_double_area": total_area2,
    }


def write_vertices_csv(path: Path, vertices):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(
            "index,raw0,x,y,z,color,u,v,nx,ny,nz,tx,ty,tz,bx,by,bz\n"
        )
        for v in vertices:
            x, y, z = v["position"]
            u, vv = v["uv"]
            nx, ny, nz = v["normal"]
            tx, ty, tz = v["tangent"]
            bx, by, bz = v["bitangent"]
            f.write(
                f'{v["index"]},0x{v["raw0"]:08X},'
                f'{x:.9g},{y:.9g},{z:.9g},'
                f'0x{v["color"]:08X},{u:.9g},{vv:.9g},'
                f'{nx:.9g},{ny:.9g},{nz:.9g},'
                f'{tx:.9g},{ty:.9g},{tz:.9g},'
                f'{bx:.9g},{by:.9g},{bz:.9g}\n'
            )


def write_indices(path: Path, indices: Iterable[int]):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for i, value in enumerate(indices):
            f.write(f"{i},{value}\n")


def normal_alignment_stats(vertices, triangles):
    accumulated = [[0.0, 0.0, 0.0] for _ in vertices]
    for a, b, c in triangles:
        ab = vec_sub(vertices[b]["position"], vertices[a]["position"])
        ac = vec_sub(vertices[c]["position"], vertices[a]["position"])
        face = vec_cross(ab, ac)
        for index in (a, b, c):
            for axis in range(3):
                accumulated[index][axis] += face[axis]

    dots = []
    for vertex, geometric in zip(vertices, accumulated):
        geometric = vec_normalize(geometric)
        if vec_len(geometric) <= 1e-20:
            continue
        packed = vec_normalize(vertex["normal"])
        dots.append(vec_dot(packed, geometric))

    if not dots:
        return {"compared_vertices": 0}

    ordered = sorted(dots)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    return {
        "compared_vertices": len(dots),
        "mean_dot": sum(dots) / len(dots),
        "median_dot": median,
        "min_dot": min(dots),
        "max_dot": max(dots),
    }


def quaternion_rotate(v, q):
    x, y, z, w = q
    vx, vy, vz = v
    return (
        (1 - 2*(y*y + z*z))*vx + 2*(x*y - z*w)*vy + 2*(x*z + y*w)*vz,
        2*(x*y + z*w)*vx + (1 - 2*(x*x + z*z))*vy + 2*(y*z - x*w)*vz,
        2*(x*z - y*w)*vx + 2*(y*z + x*w)*vy + (1 - 2*(x*x + y*y))*vz,
    )


def validate_obb(points, extents, quaternion, center):
    # The conjugate maps world-space points into the OBB local frame.
    conjugate = (-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3])
    local = [
        quaternion_rotate(vec_sub(point, center), conjugate)
        for point in points
    ]
    mins = [min(point[axis] for point in local) for axis in range(3)]
    maxs = [max(point[axis] for point in local) for axis in range(3)]
    measured_extents = [(hi - lo) / 2.0 for lo, hi in zip(mins, maxs)]
    center_residual = [(lo + hi) / 2.0 for lo, hi in zip(mins, maxs)]
    return {
        "local_min": mins,
        "local_max": maxs,
        "measured_extents": measured_extents,
        "extent_error": [
            measured - stored
            for measured, stored in zip(measured_extents, extents)
        ],
        "local_center_residual": center_residual,
        "max_abs_center_residual": max(abs(x) for x in center_residual),
    }


def find_submesh_descriptors(data: bytes, vertex_count: int, search_start: int):
    """
    Experimental 0x40-byte descriptor detector.

    Known strong pattern:
      +0x00 = 1
      +0x04 = 4
      +0x0C = base_vertex
      +0x10 = vertex_count
      +0x14 = material index

    For chr310:
      base 0,    count 1005, material 0
      base 1005, count 154,  material 1
    """
    candidates = []
    off = max(0, search_start)
    while True:
        off = data.find(SUBMESH_SIGNATURE, off)
        if off < 0:
            break
        next_search = off + 1
        if off + 0x38 > len(data):
            off = next_search
            continue

        field08, base, count, material = struct.unpack_from(">4I", data, off + 8)
        if count <= 0 or count > vertex_count:
            off = next_search
            continue
        if base >= vertex_count:
            off = next_search
            continue
        if base + count > vertex_count:
            off = next_search
            continue
        if material > 256:
            off = next_search
            continue

        candidates.append({
            "offset": off,
            "field00": 1,
            "primitive": 4,
            "primitive_count": field08,
            "base_vertex": base,
            "vertex_count": count,
            "material_index": material,
            "first_index_field_0x34": be_u32(data, off + 0x34),
        })
        off = next_search

    # Select a coherent vertex partition. This avoids accepting descriptors
    # belonging to another embedded object merely because their scalar fields
    # happen to fit the current vertex count.
    coherent_runs = []
    for first in candidates:
        if first["base_vertex"] != 0:
            continue

        run = [first]
        next_base = first["vertex_count"]
        next_index = first["primitive_count"] + 2
        while next_base < vertex_count:
            matches = [
                candidate
                for candidate in candidates
                if candidate["offset"] > run[-1]["offset"]
                and candidate["base_vertex"] == next_base
            ]
            if not matches:
                break
            candidate = min(
                matches,
                key=lambda item: (
                    item["first_index_field_0x34"] != next_index,
                    abs(item["offset"] - run[-1]["offset"] - 0x40),
                    item["offset"],
                ),
            )
            run.append(candidate)
            next_base += candidate["vertex_count"]
            first_index = candidate["first_index_field_0x34"]
            if first_index != next_index:
                first_index = next_index
            next_index = first_index + candidate["primitive_count"] + 2

        if next_base == vertex_count:
            coherent_runs.append(run)

    if not coherent_runs:
        return []

    return max(
        coherent_runs,
        key=lambda run: (
            sum(
                item["offset"] - previous["offset"] == 0x40
                for previous, item in zip(run, run[1:])
            ),
            len(run),
            -run[0]["offset"],
        ),
    )


def build_mesh_parts(indices, submeshes):
    parts = []
    cursor = 0

    for submesh_index, submesh in enumerate(submeshes):
        if submesh["primitive"] not in (3, 4):
            raise RuntimeError(
                f'Unsupported primitive code {submesh["primitive"]} '
                f"in submesh {submesh_index}."
            )

        # Lists use three indices per primitive; strips use N + 2 indices.
        index_count = (
            submesh["primitive_count"] * 3 if submesh["primitive"] == 3
            else submesh["primitive_count"] + 2
        )
        descriptor_first_index = submesh["first_index_field_0x34"]
        if 0 <= descriptor_first_index <= len(indices) - index_count:
            first_index = descriptor_first_index
            first_index_source = "descriptor_0x34"
        else:
            first_index = cursor
            first_index_source = "sequential_fallback"

        local_indices = indices[first_index:first_index + index_count]
        if len(local_indices) != index_count:
            raise RuntimeError(
                f"Submesh {submesh_index} requires {index_count} indices, "
                f"but only {len(local_indices)} remain."
            )

        local_limit = submesh["vertex_count"]
        invalid = [value for value in local_indices if value >= local_limit]
        decode_triangles = triangle_list if submesh["primitive"] == 3 else triangle_strip
        local_triangles, strip_diag = decode_triangles(
            local_indices,
            local_limit,
        )
        base = submesh["base_vertex"]
        triangles = [
            (a + base, b + base, c + base)
            for a, b, c in local_triangles
        ]

        parts.append({
            "submesh_index": submesh_index,
            "material_index": submesh["material_index"],
            "first_index": first_index,
            "first_index_source": first_index_source,
            "index_count": index_count,
            "local_index_min": min(local_indices) if local_indices else None,
            "local_index_max": max(local_indices) if local_indices else None,
            "invalid_local_index_count": len(invalid),
            "first_invalid_local_indices": invalid[:16],
            "triangles": triangles,
            "strip_diagnostics": strip_diag,
        })
        cursor = max(cursor, first_index + index_count)

    return parts, cursor


def find_map_geometry_sections(data: bytes):
    """Detect the observed map layout, validating complete vertex/index partitions.

    Map sections use 0x3C-byte descriptors, a variable first word, and an
    explicit index count at +0x38. Their streams have the same encoding as
    character meshes. Never infer a section from the header signature alone.
    """
    sections = []
    search = 0
    while True:
        off = data.find(struct.pack(">I", 7), search)
        if off < 0 or off + 16 > len(data):
            break
        search = off + 1
        _, vertex_count, buffers, index_count = struct.unpack_from(">4I", data, off)
        if buffers != 1 or vertex_count < 3 or index_count < 3:
            continue
        attribute_offset = off + 16 + index_count * 2
        position_offset = attribute_offset + vertex_count * ATTRIBUTE_STRIDE - 4
        position_end = position_offset + vertex_count * POSITION_STRIDE
        if position_end + 64 > len(data):
            continue
        # The first map descriptor starts after the trailing position word.
        descriptor_offset = position_end + 4
        if be_u32(data, descriptor_offset) != 0xFFFFFFFF:
            continue
        submeshes = []
        base = first_index = 0
        while base < vertex_count and descriptor_offset + 60 <= len(data):
            words = struct.unpack_from(">15I", data, descriptor_offset)
            primitive, primitives, local_base, count, material = words[1:6]
            if primitive not in (3, 4):
                break
            count_indices = primitives * 3 if primitive == 3 else primitives + 2
            if (local_base != base or count == 0 or base + count > vertex_count
                    or material > 256 or words[13] != first_index
                    or words[14] != count_indices
                    or first_index + count_indices > index_count):
                break
            submeshes.append({
                "offset": descriptor_offset,
                "field00": words[0],
                "primitive": primitive,
                "primitive_count": primitives,
                "base_vertex": base,
                "vertex_count": count,
                "material_index": material,
                "first_index_field_0x34": first_index,
                "descriptor_stride": 60,
            })
            base += count
            first_index += count_indices
            descriptor_offset += 60
        if base != vertex_count or first_index != index_count:
            continue
        score, diagnostics = score_position_stream(data, position_offset, vertex_count)
        if (diagnostics.get("finite_ratio") != 1.0
                or diagnostics.get("uv_reasonable_ratio") != 1.0):
            continue
        indices = decode_u16_buffer(data, off + 16, index_count)
        if any(
            value >= sm["vertex_count"]
            for sm in submeshes
            for value in indices[sm["first_index_field_0x34"]:
                sm["first_index_field_0x34"] + (
                    sm["primitive_count"] * 3 if sm["primitive"] == 3
                    else sm["primitive_count"] + 2)]
        ):
            continue
        sections.append({
            "offset": off,
            "vertex_attribute_count": 7,
            "vertex_count": vertex_count,
            "index_buffer_count": 1,
            "index_count": index_count,
            "index_offset": off + 16,
            "index_end": attribute_offset,
            "gap_to_attribute16": 0,
            "attribute16_offset": attribute_offset,
            "position_offset": position_offset,
            "position_end": position_end,
            "position_stream_score": score,
            "position_diagnostics": diagnostics,
            "submeshes": submeshes,
        })
        search = descriptor_offset
    return sections


def find_geometry_header(data: bytes, vertex_count: int, before: int):
    """
    Heuristic for the observed geometry header.

    The reference files contain four unaligned big-endian uint32 values:
      7, vertex_count, 1, index_count

    Index data starts immediately after them and consists of BE uint16 values.
    """
    prefix = struct.pack(">III", 7, vertex_count, 1)
    candidates = []
    pos = 0
    while True:
        off = data.find(prefix, pos, before)
        if off < 0:
            break
        if off + 16 <= len(data):
            index_count = be_u32(data, off + 12)
            index_offset = off + 16
            index_end = index_offset + index_count * 2
            if index_count > 0 and index_end <= before:
                candidates.append((before - index_end, off, index_count))
        pos = off + 1

    if not candidates:
        return None

    gap, off, index_count = min(candidates)
    return {
        "offset": off,
        "vertex_attribute_count": 7,
        "vertex_count": vertex_count,
        "index_buffer_count": 1,
        "index_count": index_count,
        "index_offset": off + 16,
        "index_end": off + 16 + index_count * 2,
        "gap_to_attribute16": gap,
    }
