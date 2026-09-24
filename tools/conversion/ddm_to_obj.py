#!/usr/bin/env python3
"""
ddm_to_obj.py
Geometric converter for the supported PS3 DDM v3 layout.

Purpose:
- validate the reverse-engineering hypotheses, not pretend the format is final;
- auto-detect the known position stream when possible;
- decode big-endian XYZ float32 + half-float UV from 24-byte records;
- decode the big-endian uint16 triangle-strip index buffer;
- partition that buffer using the submesh primitive counts;
- decode packed 11/11/10 normals, tangents and bitangents;
- export the reconstructed, material-separated mesh as OBJ;
- dump JSON diagnostics and CSV-like vertex/index text files.

Tested/reference files:
  chr300_c01     : sword-like model
  chr310_c01(1)  : large axe-like model

Usage:
  python tools/conversion/ddm_to_obj.py INPUT OUTPUT_DIR
  python tools/conversion/ddm_to_obj.py INPUT_DIR OUTPUT_DIR

Optional overrides:
  --vertex-count N
  --position-offset 0x2191
  --index-offset 0x33d
  --index-count 948
  --debug

Important:
This is an EXPERIMENTAL decoder. It deliberately exports multiple topology
interpretations instead of silently assuming one is correct.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path
from typing import Iterable


MAGIC = b"\x00ddm"
POSITION_STRIDE = 24
ATTRIBUTE_STRIDE = 16
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\\/\-]+$")


def be_u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def be_f32(data: bytes, off: int) -> float:
    return struct.unpack_from(">f", data, off)[0]


def be_u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def finite3(v) -> bool:
    return all(math.isfinite(x) for x in v)


def vec_sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def vec_cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def vec_len(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])


def vec_normalize(v):
    length = vec_len(v)
    if length <= 1e-20:
        return (0.0, 0.0, 0.0)
    return tuple(x / length for x in v)


def vec_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def decode_half2_be(data: bytes, off: int):
    return struct.unpack_from(">2e", data, off)


def decode_snorm(value: int, bits: int) -> float:
    sign = 1 << (bits - 1)
    if value & sign:
        value -= 1 << bits
    return max(-1.0, value / float(sign - 1))


def decode_packed_11_11_10(word: int):
    """Decode the observed X:11, Y:11, Z:10 signed normalized layout."""
    return (
        decode_snorm(word & 0x7FF, 11),
        decode_snorm((word >> 11) & 0x7FF, 11),
        decode_snorm((word >> 22) & 0x3FF, 10),
    )


def find_periodic_marker_run(data: bytes, marker=b"\x00\x00\x3c\x00", stride=16):
    """
    Find the longest run where marker occurs every `stride` bytes.

    In the two reference DDMs the marker is the fourth word of each 16-byte
    attribute record. The final occurrence is shared with the first word of
    the first 24-byte position record.
    """
    positions = []
    pos = 0
    while True:
        pos = data.find(marker, pos)
        if pos < 0:
            break
        positions.append(pos)
        pos += 1

    if not positions:
        return None

    posset = set(positions)
    best_start = None
    best_count = 0

    for p in positions:
        if p - stride in posset:
            continue
        count = 1
        q = p + stride
        while q in posset:
            count += 1
            q += stride
        if count > best_count:
            best_start = p
            best_count = count

    if best_start is None:
        return None

    return {
        "first_marker_offset": best_start,
        "marker_count": best_count,
        "attribute16_offset": best_start - 12,
        "position_offset": best_start + (best_count - 1) * stride,
        "vertex_count": best_count,
        "attribute16_count": best_count,
    }


def decode_vertices16(data: bytes, off: int, count: int):
    vertices = []
    for i in range(count):
        p = off + i * ATTRIBUTE_STRIDE
        normal_word, tangent_word, bitangent_word = struct.unpack_from(
            ">3I", data, p
        )
        marker = be_u32(data, p + 12)
        vertices.append({
            "index": i,
            "normal_word": normal_word,
            "tangent_word": tangent_word,
            "bitangent_word": bitangent_word,
            "normal": decode_packed_11_11_10(normal_word),
            "tangent": decode_packed_11_11_10(tangent_word),
            "bitangent": decode_packed_11_11_10(bitangent_word),
            "marker": marker,
        })
    return vertices


def score_position_stream(data: bytes, off: int, count: int):
    """
    Score the proposed 24-byte stream:
      +00 uint32/half2-like unknown
      +04 float X
      +08 float Y
      +0C float Z
      +10 0xFFFFFFFF (observed color)
      +14 half U
      +16 half V
    """
    if off < 0 or off + count * POSITION_STRIDE > len(data):
        return -1e30, {}

    finite = 0
    white = 0
    uv_reasonable = 0
    points = []

    for i in range(count):
        p = off + i * POSITION_STRIDE
        xyz = struct.unpack_from(">3f", data, p + 4)
        if finite3(xyz) and max(abs(x) for x in xyz) < 1e7:
            finite += 1
            points.append(xyz)

        if be_u32(data, p + 16) == 0xFFFFFFFF:
            white += 1

        try:
            u, v = decode_half2_be(data, p + 20)
            if math.isfinite(u) and math.isfinite(v) and abs(u) < 100 and abs(v) < 100:
                uv_reasonable += 1
        except Exception:
            pass

    if not points:
        return -1e30, {}

    bbox = []
    for axis in range(3):
        vals = [p[axis] for p in points]
        bbox.append((min(vals), max(vals)))

    ranges = [b-a for a, b in bbox]
    nonflat = sum(r > 1e-5 for r in ranges)

    score = (
        finite / count * 5.0
        + white / count * 3.0
        + uv_reasonable / count * 2.0
        + nonflat
    )

    return score, {
        "finite_ratio": finite / count,
        "white_ratio": white / count,
        "uv_reasonable_ratio": uv_reasonable / count,
        "bbox": bbox,
    }


def decode_vertices24(data: bytes, off: int, count: int):
    vertices = []
    for i in range(count):
        p = off + i * POSITION_STRIDE

        raw0 = be_u32(data, p)
        xyz = struct.unpack_from(">3f", data, p + 4)
        color = be_u32(data, p + 16)
        uv = decode_half2_be(data, p + 20)

        vertices.append({
            "index": i,
            "raw0": raw0,
            "position": xyz,
            "color": color,
            "uv": uv,
        })
    return vertices


def extract_length_prefixed_strings(data: bytes, end: int):
    """Find DDM strings stored as BE uint32 length + NUL-terminated ASCII."""
    strings = []
    for off in range(0, max(0, end - 5)):
        length = be_u32(data, off)
        if length < 2 or length > 128 or off + 4 + length > end:
            continue
        raw = data[off + 4:off + 4 + length]
        if raw[-1] != 0:
            continue
        text = raw[:-1].decode("ascii", errors="ignore")
        if not text or not SAFE_NAME_RE.fullmatch(text):
            continue
        strings.append({
            "offset": off,
            "end": off + 4 + length,
            "length": length,
            "text": text,
        })
    return strings


def parse_materials(data: bytes, end: int, expected_count: int):
    """Parse the observed material-name/texture-reference string skeleton.

    A material name is immediately followed by its first texture string. The
    uint32 before the first material name is the material count.
    """
    strings = extract_length_prefixed_strings(data, end)
    if expected_count <= 0:
        return []

    first_string_index = None
    for i in range(len(strings) - 1):
        item = strings[i]
        if item["end"] != strings[i + 1]["offset"]:
            continue
        if item["offset"] < 4:
            continue
        if be_u32(data, item["offset"] - 4) == expected_count:
            first_string_index = i
            break

    if first_string_index is None:
        return []

    material_string_indices = []
    for i in range(first_string_index, len(strings) - 1):
        if strings[i]["end"] == strings[i + 1]["offset"]:
            material_string_indices.append(i)
            if len(material_string_indices) == expected_count:
                break

    if len(material_string_indices) != expected_count:
        return []

    materials = []
    for material_index, string_index in enumerate(material_string_indices):
        stop = (
            material_string_indices[material_index + 1]
            if material_index + 1 < len(material_string_indices)
            else len(strings)
        )
        materials.append({
            "index": material_index,
            "name": strings[string_index]["text"],
            "name_offset": strings[string_index]["offset"],
            "texture_references": [
                item["text"]
                for item in strings[string_index + 1:stop]
            ],
        })
    return materials


def texture_storage_names(reference: str):
    names = [reference]
    match = re.fullmatch(r"(.+)_c(?:\d+)?", reference, re.IGNORECASE)
    if match:
        names.append(match.group(1))
    return list(dict.fromkeys(names))


def resolve_texture_path(reference: str, model_path: Path, texture_root=None):
    folder_name = reference.split("_", 1)[0]
    roots = []
    if texture_root is not None:
        roots.append(Path(texture_root))
    roots.extend((model_path.parent, model_path.parent.parent))

    candidates = []
    for root in roots:
        for name in texture_storage_names(reference):
            candidates.extend((root / name, root / folder_name / name))

    seen = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False)).lower()
        if key in seen:
            continue
        seen.add(key)
        if not candidate.is_file():
            continue
        with candidate.open("rb") as stream:
            if stream.read(4) == b"\x00xet":
                return candidate
    return None


def image_content_stats(image):
    sample = image.convert("RGB")
    sample.thumbnail((256, 256))
    if hasattr(sample, "get_flattened_data"):
        pixels = list(sample.get_flattened_data())
    else:
        pixels = list(sample.getdata())
    total = max(1, len(pixels))
    means = [sum(pixel[channel] for pixel in pixels) / total for channel in range(3)]
    blue_ratio = sum(
        b > 140 and b > r + 20 and b > g + 10
        for r, g, b in pixels
    ) / total
    red_mask_ratio = sum(
        r > 24 and r > g * 1.5 and r > b * 1.5
        for r, g, b in pixels
    ) / total
    grayscale_ratio = sum(
        max(r, g, b) - min(r, g, b) < 15
        for r, g, b in pixels
    ) / total
    return {
        "mean_rgb": means,
        "blue_normal_ratio": blue_ratio,
        "red_mask_ratio": red_mask_ratio,
        "grayscale_ratio": grayscale_ratio,
    }


def convert_xet_texture(source: Path, destination: Path):
    try:
        from PIL import Image
        try:
            from . import xet_to_png as xet
        except ImportError:
            import xet_to_png as xet
    except ImportError as exc:
        raise RuntimeError(
            "Pillow and tools/conversion/xet_to_png.py are required "
            "for texture conversion."
        ) from exc

    data = source.read_bytes()
    if len(data) < 0x84 or data[:4] != xet.MAGIC:
        raise RuntimeError(f"{source} is not a supported XET texture.")
    width, height = struct.unpack_from(">2H", data, 0x80)
    if not width or not height:
        raise RuntimeError(f"{source} has invalid dimensions.")
    offset, texture_format = xet.find_offset_and_format(data, width, height)
    payload = data[offset:]
    if texture_format == "DXT5":
        rgba = xet.decode_dxt5_xet(
            payload[:xet.dxt5_size(width, height)],
            width,
            height,
        )
    else:
        rgba = xet.decode_dxt1(
            payload[:xet.dxt1_size(width, height)],
            width,
            height,
        )
    image = Image.frombytes("RGBA", (width, height), rgba)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return {
        "width": width,
        "height": height,
        "format": texture_format,
        "data_offset": offset,
        "content": image_content_stats(image),
    }


def classify_material_textures(textures):
    for texture in textures:
        stats = texture.get("conversion", {}).get("content", {})
        if stats.get("blue_normal_ratio", 0.0) >= 0.80:
            texture["role"] = "normal"
        elif stats.get("red_mask_ratio", 0.0) >= 0.60:
            texture["role"] = "specular_mask"
        else:
            texture["role"] = "auxiliary"

    diffuse_candidates = [
        texture
        for texture in textures
        if texture.get("role") == "auxiliary" and texture.get("conversion")
    ]
    if diffuse_candidates:
        diffuse = max(
            diffuse_candidates,
            key=lambda texture: (
                texture["conversion"]["width"] * texture["conversion"]["height"],
                -texture["conversion"]["content"].get("grayscale_ratio", 0.0),
            ),
        )
        diffuse["role"] = "diffuse"


def resolve_material_textures(materials, model_path, out_dir, texture_root=None):
    texture_dir = out_dir / "textures"
    conversion_cache = {}
    for material in materials:
        textures = []
        for slot, reference in enumerate(material["texture_references"]):
            source = resolve_texture_path(reference, model_path, texture_root)
            texture = {
                "slot": slot,
                "reference": reference,
                "source": str(source) if source else None,
                "output": None,
                "conversion": None,
                "role": "unresolved",
            }
            if source is not None:
                cache_key = str(source.resolve()).lower()
                if cache_key not in conversion_cache:
                    destination = texture_dir / f"{source.stem}.png"
                    conversion_cache[cache_key] = {
                        "output": destination,
                        "conversion": convert_xet_texture(source, destination),
                    }
                cached = conversion_cache[cache_key]
                texture["output"] = cached["output"].relative_to(out_dir).as_posix()
                texture["conversion"] = cached["conversion"]
            textures.append(texture)
        classify_material_textures(textures)
        material["textures"] = textures
    return materials


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


def decode_u16_buffer(data: bytes, off: int, count: int):
    if off < 0 or off + count * 2 > len(data):
        return []
    return list(struct.unpack_from(">" + "H" * count, data, off))


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


def material_export_name(material):
    name = material.get("name") or f'material_{material["index"]}'
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def write_obj(path: Path, vertices, mesh_parts, materials, object_name: str):
    materials_by_index = {material["index"]: material for material in materials}
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Experimental DDM geometry decoder\n")
        f.write("# Primitive code 4 interpreted as triangle strips.\n")
        f.write("mtllib materials.mtl\n")
        f.write(f"o {object_name}\n")

        for v in vertices:
            x, y, z = v["position"]
            f.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")

        for v in vertices:
            u, vv = v["uv"]
            # XET images use a top-left origin; OBJ UVs use bottom-left.
            f.write(f"vt {u:.9g} {1.0 - vv:.9g}\n")

        for v in vertices:
            x, y, z = vec_normalize(v["normal"])
            f.write(f"vn {x:.9g} {y:.9g} {z:.9g}\n")

        f.write("s 1\n")
        for part in mesh_parts:
            part_index = part["submesh_index"]
            material_index = part["material_index"]
            material = materials_by_index.get(
                material_index,
                {"index": material_index, "name": f"material_{material_index}"},
            )
            f.write(f"g submesh_{part_index}\n")
            f.write(f"usemtl {material_export_name(material)}\n")
            for a, b, c in part["triangles"]:
                a += 1
                b += 1
                c += 1
                f.write(
                    f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n"
                )


def write_mtl(path: Path, materials):
    palette = (
        (0.75, 0.75, 0.75),
        (0.80, 0.45, 0.25),
        (0.30, 0.60, 0.85),
        (0.45, 0.80, 0.40),
    )
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# DDM materials and decoded XET texture references\n")
        for material in materials:
            material_index = material["index"]
            r, g, b = palette[material_index % len(palette)]
            f.write(f"newmtl {material_export_name(material)}\n")
            f.write(f'# DDM material index: {material_index}\n')
            for texture in material.get("textures", []):
                f.write(
                    f'# slot {texture["slot"]}: {texture["reference"]} '
                    f'[{texture["role"]}]\n'
                )
            diffuse = next(
                (
                    texture
                    for texture in material.get("textures", [])
                    if texture["role"] == "diffuse" and texture["output"]
                ),
                None,
            )
            normal = next(
                (
                    texture
                    for texture in material.get("textures", [])
                    if texture["role"] == "normal" and texture["output"]
                ),
                None,
            )
            specular = next(
                (
                    texture
                    for texture in material.get("textures", [])
                    if texture["role"] == "specular_mask" and texture["output"]
                ),
                None,
            )
            if diffuse:
                f.write("Kd 1.000 1.000 1.000\n")
                f.write(f'map_Kd {diffuse["output"]}\n')
            else:
                f.write(f"Kd {r:.3f} {g:.3f} {b:.3f}\n")
            f.write("Ka 0.050 0.050 0.050\n")
            f.write("Ks 0.150 0.150 0.150\n\n")
            if specular:
                f.write(f'map_Ks {specular["output"]}\n')
            if normal:
                # `norm` is widely supported as a tangent-space extension;
                # map_Bump keeps compatibility with simpler OBJ importers.
                f.write(f'norm {normal["output"]}\n')
                f.write(f'map_Bump -bm 1.0 {normal["output"]}\n')
            f.write("illum 2\n\n")


def write_point_obj(path: Path, vertices, object_name: str):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# DDM position stream only\n")
        f.write(f"o {object_name}_points\n")
        for v in vertices:
            x, y, z = v["position"]
            f.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")
        for i in range(len(vertices)):
            f.write(f"p {i+1}\n")


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
    result = []

    for off in range(max(0, search_start), len(data) - 0x18):
        try:
            f0 = be_u32(data, off + 0x00)
            primitive = be_u32(data, off + 0x04)
            field08 = be_u32(data, off + 0x08)
            base = be_u32(data, off + 0x0C)
            count = be_u32(data, off + 0x10)
            material = be_u32(data, off + 0x14)
        except struct.error:
            continue

        if f0 != 1 or primitive != 4:
            continue
        if count <= 0 or count > vertex_count:
            continue
        if base >= vertex_count:
            continue
        if base + count > vertex_count:
            continue
        if material > 256:
            continue

        result.append({
            "offset": off,
            "field00": f0,
            "primitive": primitive,
            "primitive_count": field08,
            "base_vertex": base,
            "vertex_count": count,
            "material_index": material,
            "first_index_field_0x34": (
                be_u32(data, off + 0x34)
                if off + 0x38 <= len(data)
                else None
            ),
        })

    # Remove overlapping accidental matches.
    filtered = []
    last = -0x1000
    for r in result:
        if r["offset"] - last >= 0x18:
            filtered.append(r)
            last = r["offset"]

    return filtered


def build_mesh_parts(indices, submeshes):
    parts = []
    cursor = 0

    for submesh_index, submesh in enumerate(submeshes):
        if submesh["primitive"] != 4:
            raise RuntimeError(
                f'Unsupported primitive code {submesh["primitive"]} '
                f"in submesh {submesh_index}."
            )

        # For an ordinary triangle strip, N primitives require N + 2 indices.
        index_count = submesh["primitive_count"] + 2
        local_indices = indices[cursor:cursor + index_count]
        if len(local_indices) != index_count:
            raise RuntimeError(
                f"Submesh {submesh_index} requires {index_count} indices, "
                f"but only {len(local_indices)} remain."
            )

        local_limit = submesh["vertex_count"]
        invalid = [value for value in local_indices if value >= local_limit]
        local_triangles, strip_diag = triangle_strip(
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
            "first_index": cursor,
            "index_count": index_count,
            "local_index_min": min(local_indices) if local_indices else None,
            "local_index_max": max(local_indices) if local_indices else None,
            "invalid_local_index_count": len(invalid),
            "first_invalid_local_indices": invalid[:16],
            "triangles": triangles,
            "strip_diagnostics": strip_diag,
        })
        cursor += index_count

    return parts, cursor


def analyze_file(path: Path, out_root: Path, args):
    data = path.read_bytes()

    if len(data) < 8 or data[:4] != MAGIC:
        print(f"[SKIP] {path.name}: not a DDM v3-like file")
        return

    version = be_u32(data, 4)
    out_dir = out_root / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    periodic = find_periodic_marker_run(data)

    if args.vertex_count is not None:
        vertex_count = args.vertex_count
    elif periodic:
        vertex_count = periodic["vertex_count"]
    else:
        raise RuntimeError("Could not auto-detect vertex count; use --vertex-count.")

    if args.position_offset is not None:
        position_offset = args.position_offset
    elif periodic:
        position_offset = periodic["position_offset"]
    else:
        raise RuntimeError("Could not auto-detect position stream; use --position-offset.")

    score, position_diag = score_position_stream(
        data, position_offset, vertex_count
    )

    if score < 0:
        raise RuntimeError(
            f"Position stream candidate 0x{position_offset:X} is invalid."
        )

    vertices = decode_vertices24(data, position_offset, vertex_count)

    attribute16_offset = (
        periodic["attribute16_offset"] if periodic else None
    )

    # Geometry/index header.
    header = None
    if args.index_offset is None or args.index_count is None:
        header = find_geometry_header(
            data,
            vertex_count,
            attribute16_offset if attribute16_offset is not None else position_offset,
        )

    index_offset = args.index_offset
    index_count = args.index_count

    if index_offset is None and header:
        index_offset = header["index_offset"]
    if index_count is None and header:
        index_count = header["index_count"]

    # Known fallback signatures for the two reference files.
    # These are used only if generic detection fails.
    if index_offset is None or index_count is None:
        if vertex_count == 367:
            index_offset = 0x33D if index_offset is None else index_offset
            index_count = 0x3B4 if index_count is None else index_count
        elif vertex_count == 1159:
            index_offset = 0x4D7 if index_offset is None else index_offset
            index_count = 0x9A4 if index_count is None else index_count

    if index_offset is None or index_count is None:
        raise RuntimeError("Could not locate the index buffer.")

    indices = decode_u16_buffer(data, index_offset, index_count)
    if len(indices) != index_count:
        raise RuntimeError("The detected index buffer exceeds the file.")

    detected_attribute16_offset = index_offset + index_count * 2
    if attribute16_offset is None:
        attribute16_offset = detected_attribute16_offset
    attributes = decode_vertices16(data, attribute16_offset, vertex_count)
    for vertex, attribute in zip(vertices, attributes):
        vertex.update({
            "normal": attribute["normal"],
            "tangent": attribute["tangent"],
            "bitangent": attribute["bitangent"],
            "attribute_marker": attribute["marker"],
        })

    # Search descriptors after the position stream.
    position_end = position_offset + vertex_count * POSITION_STRIDE
    submeshes = find_submesh_descriptors(
        data,
        vertex_count,
        max(0, position_end - 0x20),
    )
    if not submeshes:
        raise RuntimeError("No submesh descriptor found.")

    material_count = max(
        submesh["material_index"] for submesh in submeshes
    ) + 1
    materials = parse_materials(
        data,
        header["offset"] if header else index_offset - 16,
        material_count,
    )
    if len(materials) != material_count:
        materials = [
            {
                "index": material_index,
                "name": f"material_{material_index}",
                "name_offset": None,
                "texture_references": [],
            }
            for material_index in range(material_count)
        ]
    if not args.no_textures:
        materials = resolve_material_textures(
            materials,
            path,
            out_dir,
            args.texture_root,
        )
    else:
        for material in materials:
            material["textures"] = []

    mesh_parts, consumed_indices = build_mesh_parts(indices, submeshes)
    all_triangles = [
        triangle
        for part in mesh_parts
        for triangle in part["triangles"]
    ]

    points = [v["position"] for v in vertices]
    max_radius = max(vec_len(p) for p in points)

    bbox = []
    for axis in range(3):
        vals = [p[axis] for p in points]
        bbox.append([min(vals), max(vals)])

    report = {
        "file": str(path),
        "file_size": len(data),
        "version": version,
        "auto_periodic_detection": periodic,
        "vertex_count": vertex_count,
        "position_offset": position_offset,
        "position_stride": POSITION_STRIDE,
        "attribute16_offset": attribute16_offset,
        "attribute16_stride": ATTRIBUTE_STRIDE,
        "attribute16_boundary_matches_index_end": (
            attribute16_offset == detected_attribute16_offset
        ),
        "attribute_marker_match_count": sum(
            attribute["marker"] == 0x00003C00
            for attribute in attributes
        ),
        "position_stream_score": score,
        "position_diagnostics": position_diag,
        "position_end": position_end,
        "bbox": bbox,
        "max_radius_from_origin": max_radius,
        "header_candidate": header,
        "materials": materials,
        "submeshes": submeshes,
        "index_buffer": {
            "offset": index_offset,
            "count": index_count,
            "byte_size": index_count * 2,
            "endianness": "big",
            "min": min(indices),
            "max": max(indices),
            "consumed_by_submeshes": consumed_indices,
            "unconsumed_count": len(indices) - consumed_indices,
        },
        "mesh_parts": [],
    }

    # Useful check against the suspected bounding sphere radius @ 0x90.
    if len(data) >= 0x94:
        try:
            sphere_radius = be_f32(data, 0x90)
            report["suspected_bounding_sphere_radius_0x90"] = sphere_radius
            report["radius_error"] = abs(sphere_radius - max_radius)
        except Exception:
            pass

    # Suspected OBB values.
    if len(data) >= 0x144:
        try:
            extents = struct.unpack_from(">3f", data, 0x118)
            quaternion = struct.unpack_from(">4f", data, 0x128)
            center = struct.unpack_from(">3f", data, 0x138)
            report["suspected_obb"] = {
                "extents_0x118": list(extents),
                "field_0x124": be_f32(data, 0x124),
                "quaternion_0x128": list(quaternion),
                "center_0x138": list(center),
                "validation": validate_obb(
                    points,
                    extents,
                    quaternion,
                    center,
                ),
            }
        except Exception:
            pass

    # Always export the position cloud. If this looks like the sword/axe,
    # the position stream hypothesis is immediately validated.
    write_point_obj(
        out_dir / "positions_only.obj",
        vertices,
        path.stem,
    )
    write_vertices_csv(
        out_dir / "vertices.csv",
        vertices,
    )
    write_indices(out_dir / "indices.csv", indices)
    write_mtl(out_dir / "materials.mtl", materials)
    write_obj(
        out_dir / "mesh.obj",
        vertices,
        mesh_parts,
        materials,
        path.stem,
    )

    for part in mesh_parts:
        topology = topology_stats(vertices, part["triangles"])
        report["mesh_parts"].append({
            key: value
            for key, value in part.items()
            if key != "triangles"
        } | {
            "triangle_count": len(part["triangles"]),
            "topology": topology,
        })

    report["normal_validation"] = normal_alignment_stats(
        vertices,
        all_triangles,
    )

    with (out_dir / "analysis.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(report, f, indent=2)

    print(f"\n[{path.name}]")
    print(f"  version          : {version}")
    print(f"  vertices         : {vertex_count}")
    print(f"  position stream  : 0x{position_offset:X}")
    print(f"  position end     : 0x{position_end:X}")
    print(f"  attribute stream : 0x{attribute16_offset:X}")
    print(f"  position score   : {score:.3f}")
    print(f"  max radius       : {max_radius:.6f}")

    if "suspected_bounding_sphere_radius_0x90" in report:
        print(
            "  radius @ 0x90    : "
            f'{report["suspected_bounding_sphere_radius_0x90"]:.6f}'
        )
        print(
            "  radius error     : "
            f'{report["radius_error"]:.9g}'
        )

    print(f"  submeshes found  : {len(submeshes)}")
    for i, sm in enumerate(submeshes):
        print(
            f'    #{i}: off=0x{sm["offset"]:X} '
            f'prim={sm["primitive"]} '
            f'primitive_count={sm["primitive_count"]} '
            f'base={sm["base_vertex"]} '
            f'count={sm["vertex_count"]} '
            f'material={sm["material_index"]}'
        )

    print(f"  materials found  : {len(materials)}")
    for material in materials:
        texture_summary = ", ".join(
            f'{texture["reference"]}:{texture["role"]}'
            for texture in material.get("textures", [])
        )
        print(
            f'    #{material["index"]}: {material["name"]}'
            + (f" [{texture_summary}]" if texture_summary else "")
        )

    ib = report["index_buffer"]
    print(
        f'  indices          : off=0x{ib["offset"]:X} '
        f'count={ib["count"]} bytes=0x{ib["byte_size"]:X} '
        f'consumed={ib["consumed_by_submeshes"]}'
    )
    for part in report["mesh_parts"]:
        print(
            f'    SM{part["submesh_index"]}: '
            f'first={part["first_index"]} '
            f'indices={part["index_count"]} '
            f'triangles={part["triangle_count"]} '
            f'local_range={part["local_index_min"]}..{part["local_index_max"]} '
            f'invalid={part["invalid_local_index_count"]}'
        )

    normals = report["normal_validation"]
    print(
        "  normal agreement : "
        f'mean={normals.get("mean_dot", 0.0):.6f} '
        f'median={normals.get("median_dot", 0.0):.6f}'
    )

    print(f"  output           : {out_dir}")


def parse_int(value: str):
    return int(value, 0)


def iter_input_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        for p in sorted(path.iterdir()):
            if p.is_file():
                yield p
    else:
        raise FileNotFoundError(path)


def main():
    ap = argparse.ArgumentParser(
        description="Experimental PS3 DDM to OBJ/MTL converter"
    )
    ap.add_argument("input", type=Path, help="DDM file or directory")
    ap.add_argument("output", type=Path, help="Output directory")

    ap.add_argument("--vertex-count", type=int)
    ap.add_argument("--position-offset", type=parse_int)
    ap.add_argument("--index-offset", type=parse_int)
    ap.add_argument(
        "--index-count",
        "--index-size",
        dest="index_count",
        type=parse_int,
        help=(
            "Number of uint16 indices. --index-size is retained as a "
            "deprecated compatibility alias."
        ),
    )
    ap.add_argument(
        "--texture-root",
        type=Path,
        help=(
            "Root containing referenced texture folders (normally inferred "
            "from the input path)."
        ),
    )
    ap.add_argument(
        "--no-textures",
        action="store_true",
        help="Parse material names but do not resolve or convert XET textures.",
    )
    ap.add_argument("--debug", action="store_true")

    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    processed = 0

    for path in iter_input_files(args.input):
        try:
            data = path.read_bytes()
            if len(data) < 8 or data[:4] != MAGIC:
                if args.debug:
                    print(f"[SKIP] {path}: magic mismatch")
                continue

            analyze_file(path, args.output, args)
            processed += 1

        except Exception as exc:
            print(f"[ERROR] {path}: {exc}")
            if args.debug:
                raise

    if processed == 0:
        print("No DDM file decoded.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
