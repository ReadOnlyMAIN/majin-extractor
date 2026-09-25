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
- combine validated map sections and decode their triangle lists/strips;
- decode packed 11/11/10 normals, tangents and bitangents;
- recover the observed legacy Phong material parameters;
- export maps as separate connected objects in GLB (default), or OBJ/MTL;
- optionally dump JSON diagnostics and CSV-like vertex/index text files.

Tested/reference files:
  chr300_c01     : sword-like model
  chr310_c01(1)  : large axe-like model
  map101_R0     : three map sections with 0x3C-byte descriptors

Usage:
  python tools/conversion/ddm_to_obj.py INPUT OUTPUT_DIR
  python tools/conversion/ddm_to_obj.py INPUT_DIR OUTPUT_DIR

Optional overrides:
  --recursive
  --final [true|false]
  --scale 0.01
  --vertex-count N
  --position-offset 0x2191
  --index-offset 0x33d
  --index-count 948
  --debug

Important:
This is an EXPERIMENTAL decoder. Unsupported DDM variants are reported instead
of being decoded with reference-file-specific offsets.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import tempfile

try:
    from .glb_export import write_glb, write_skinned_glb
except ImportError:
    from glb_export import write_glb, write_skinned_glb
from pathlib import Path
from typing import Iterable


MAGIC = b"\x00ddm"
POSITION_STRIDE = 24
ATTRIBUTE_STRIDE = 16
DEFAULT_EXPORT_SCALE = 0.01
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\\/\-]+$")
SUBMESH_SIGNATURE = struct.pack(">II", 1, 4)


class UnsupportedDDMVariant(RuntimeError):
    """A valid DDM whose geometry layout is not implemented yet."""


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
        material_strings = strings[string_index:stop]
        parameter_end = (
            strings[stop]["offset"] if stop < len(strings) else end
        )
        phong = find_phong_parameters(
            data,
            max(item["end"] for item in material_strings),
            parameter_end,
        )
        materials.append({
            "index": material_index,
            "name": strings[string_index]["text"],
            "name_offset": strings[string_index]["offset"],
            "texture_references": [
                item["text"]
                for item in material_strings[1:]
            ],
            "phong": phong,
            "pbr_estimate": phong_to_pbr_estimate(phong),
        })
    return materials


def find_phong_parameters(data: bytes, start: int, end: int):
    """Find the observed Kd/Ka/Ks/Ns sequence in a material record.

    The serialized record contains nine consecutive big-endian float32 color
    components, an incompletely understood scalar, and a Phong shininess
    exponent. Its absolute position varies with the texture slots and shader
    variant, so the parser validates the value sequence instead of relying on
    a reference offset.

    This remains a structural heuristic. Returning ``None`` is preferable to
    assigning plausible-looking values from an unsupported material variant.
    """
    candidates = []
    value_size = 11 * 4
    first_offset = max(0, start)
    last_offset = end - value_size
    if last_offset < first_offset:
        return None

    for offset in range(first_offset, last_offset + 1):
        values = struct.unpack_from(">11f", data, offset)
        colors = values[:9]
        trailing_scalar = values[9]
        shininess = values[10]

        if not all(math.isfinite(value) for value in values):
            continue
        if not all(-2.001 <= value <= 4.0 for value in colors):
            continue
        if sum(abs(value) for value in colors) < 0.01:
            continue
        if not -0.001 <= trailing_scalar <= 1.001:
            continue
        if not 2.0 <= shininess <= 512.0:
            continue

        # Real color triplets tend to have related RGB components. This score
        # rejects byte-shifted interpretations and integer flags seen as tiny
        # floats while retaining intentionally tinted materials.
        score = sum(
            abs(colors[index] - colors[index + 1])
            for index in (0, 1, 3, 4, 6, 7)
        )
        score += 4.0 * sum(
            max(0.0, value - 1.01) for value in colors[:6]
        )
        score -= min(shininess, 128.0) / 10000.0
        candidates.append((score, offset, values))

    if not candidates:
        return None

    _, offset, values = min(candidates, key=lambda candidate: candidate[0])
    return {
        "offset": offset,
        "diffuse": list(values[0:3]),
        "ambient": list(values[3:6]),
        "specular": list(values[6:9]),
        "unknown_scalar_after_specular": values[9],
        "shininess": values[10],
        "encoding": "legacy_phong_candidate",
    }


def phong_to_pbr_estimate(phong):
    """Return explicitly derived PBR metadata without inventing source data."""
    if not phong:
        return None

    shininess = phong["shininess"]
    roughness = min(1.0, max(0.0, math.sqrt(2.0 / (shininess + 2.0))))
    return {
        "metallic": None,
        "roughness": roughness,
        "roughness_source": "derived_from_phong_shininess",
        "roughness_formula": "sqrt(2 / (Ns + 2))",
    }


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
        if not texture.get("conversion"):
            texture["role"] = "unresolved"
            continue
        stats = texture["conversion"].get("content", {})
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


def find_skinned_geometry_header(data: bytes):
    """Identify the observed eight-attribute character layout.

    Unlike the supported static layout, this header begins with the buffer
    count and includes an explicit vertex stride. Detection is deliberately
    strict so arbitrary metadata is never mislabeled as skinned geometry.
    """
    signature = struct.pack(">I", 8)
    search = 8
    while True:
        attribute_offset = data.find(signature, search)
        if attribute_offset < 0:
            return None
        search = attribute_offset + 1
        offset = attribute_offset - 8
        if offset + 24 > len(data):
            continue
        sections, submeshes, attributes, vertices, palette_size, indices = (
            struct.unpack_from(">6I", data, offset)
        )
        if (
            1 <= sections <= 64
            and 1 <= submeshes <= 64
            and attributes == 8
            and 3 <= vertices <= 1_000_000
            and 1 <= palette_size <= 256
            and 3 <= indices <= 10_000_000
            and offset + 24 + indices * 2 + vertices * 44 <= len(data)
        ):
            return {
                "offset": offset,
                "section_count": sections,
                "first_submesh_count": submeshes,
                "vertex_attribute_count": attributes,
                "vertex_count": vertices,
                "bone_palette_count": palette_size,
                "vertex_stride": 28,
                "index_count": indices,
            }


def decode_skinned_skeleton(data: bytes, transform_bone_ids=None):
    """Decode the observed compact bone-id/parent/TR layout near DDM offset B0."""
    if len(data) < 0xB4:
        raise RuntimeError("Skinned DDM is too small for its skeleton header.")
    transform_count = be_u32(data, 0xB0)
    if not 1 <= transform_count <= 1024:
        raise RuntimeError(f"Invalid skeleton transform count {transform_count}.")
    id_offset = 0xB4
    padded_count = (transform_count + 3) & ~3
    parent_offset = id_offset + padded_count
    translation_offset = parent_offset + padded_count
    rotation_offset = translation_offset + transform_count * 16
    flags_offset = rotation_offset + transform_count * 16
    if flags_offset + transform_count * 4 > len(data):
        raise RuntimeError("Skeleton arrays exceed the DDM file.")

    raw_ids = list(data[id_offset:id_offset + padded_count])
    raw_parents = list(data[parent_offset:parent_offset + transform_count])
    # Some files insert duplicate zero bytes before their final global ids.
    # Reading the padded id area and preserving unique values reconstructs the
    # declared transform count across chr300/302/310/314/330 variants.
    bone_ids = []
    seen = set()
    for bone_id in raw_ids:
        if bone_id in seen:
            continue
        seen.add(bone_id)
        bone_ids.append(bone_id)
    if len(bone_ids) != transform_count:
        raise RuntimeError(
            f"Skeleton declares {transform_count} transforms but exposes "
            f"{len(bone_ids)} unique bone ids."
        )
    parent_by_id = {
        bone_id: raw_parents[index]
        for index, bone_id in enumerate(bone_ids)
    }
    if transform_bone_ids is None:
        transform_bone_ids = bone_ids
    else:
        transform_bone_ids = list(transform_bone_ids)
        if (
            len(transform_bone_ids) != transform_count
            or len(set(transform_bone_ids)) != transform_count
            or set(transform_bone_ids) != set(bone_ids)
        ):
            raise RuntimeError(
                "Motion skeleton order does not match the DDM bone identifiers."
            )
    id_to_joint = {
        bone_id: index for index, bone_id in enumerate(transform_bone_ids)
    }
    joints = []
    for index, bone_id in enumerate(transform_bone_ids):
        parent_id = parent_by_id[bone_id]
        if parent_id == 0xFF:
            parent = None
        elif parent_id in id_to_joint:
            parent = id_to_joint[parent_id]
        else:
            raise RuntimeError(
                f"Bone {bone_id} references unknown parent id {parent_id}."
            )
        translation = struct.unpack_from(">3f", data, translation_offset + index * 16)
        rotation = struct.unpack_from(">4f", data, rotation_offset + index * 16)
        if not finite3(translation) or not all(math.isfinite(x) for x in rotation):
            raise RuntimeError(f"Bone {bone_id} has a non-finite bind transform.")
        norm = math.sqrt(sum(x * x for x in rotation))
        if not 0.99 <= norm <= 1.01:
            raise RuntimeError(f"Bone {bone_id} has invalid quaternion norm {norm}.")
        joints.append({
            "index": index,
            "global_id": bone_id,
            "parent": parent,
            "translation": translation,
            "rotation": tuple(x / norm for x in rotation),
            "name": f"bone_{bone_id:03d}",
        })
    return {
        "transform_count": transform_count,
        "joint_count": len(joints),
        "joints": joints,
        "id_to_joint": id_to_joint,
        "transform_bone_ids": transform_bone_ids,
    }


def decode_skinned_geometry(data: bytes, header, skeleton):
    """Decode all 8-attribute sections following a skinned geometry group."""
    group_offset = header["offset"]
    section_count = be_u32(data, group_offset)
    if not 1 <= section_count <= 64:
        raise RuntimeError(f"Invalid skinned section count {section_count}.")
    cursor = group_offset + 4
    vertices = []
    mesh_parts = []
    sections = []
    id_to_joint = skeleton["id_to_joint"]
    for section_index in range(section_count):
        if cursor + 20 > len(data):
            if not any(data[cursor:]):
                break
            raise RuntimeError("Truncated skinned section header.")
        descriptor_count, attributes, vertex_count, palette_count, index_count = (
            struct.unpack_from(">5I", data, cursor)
        )
        section_offset = cursor
        cursor += 20
        if attributes != 8 or not vertex_count or not index_count:
            raise RuntimeError(
                f"Unsupported skinned section layout at 0x{section_offset:X}."
            )
        indices = decode_u16_buffer(data, cursor, index_count)
        if len(indices) != index_count:
            raise RuntimeError("Skinned index buffer exceeds the file.")
        index_offset = cursor
        cursor += index_count * 2
        attribute_offset = cursor
        attributes16 = decode_vertices16(data, cursor, vertex_count)
        cursor += vertex_count * ATTRIBUTE_STRIDE
        position_offset = cursor
        section_vertices = []
        for local_index in range(vertex_count):
            offset = cursor + local_index * 28
            if offset + 28 > len(data):
                raise RuntimeError("Skinned vertex buffer exceeds the file.")
            position = struct.unpack_from(">3f", data, offset)
            color = be_u32(data, offset + 12)
            uv = decode_half2_be(data, offset + 16)
            palette_indices = tuple(data[offset + 20:offset + 24])
            raw_weights = tuple(data[offset + 24:offset + 28])
            if sum(raw_weights) != 255:
                raise RuntimeError(
                    f"Skinned vertex {local_index} weights sum to {sum(raw_weights)}."
                )
            section_vertices.append({
                "index": len(vertices) + local_index,
                "position": position,
                "color": color,
                "uv": uv,
                "normal": attributes16[local_index]["normal"],
                "tangent": attributes16[local_index]["tangent"],
                "bitangent": attributes16[local_index]["bitangent"],
                "joint_palette_indices": palette_indices,
                "weights": tuple(value / 255.0 for value in raw_weights),
            })
        cursor += vertex_count * 28
        if cursor + palette_count * 4 > len(data):
            raise RuntimeError("Skinned bone palette exceeds the file.")
        palette_ids = struct.unpack_from(">" + "I" * palette_count, data, cursor)
        palette_offset = cursor
        cursor += palette_count * 4
        try:
            palette = tuple(id_to_joint[bone_id] for bone_id in palette_ids)
        except KeyError as exc:
            raise RuntimeError(f"Bone palette references unknown id {exc.args[0]}.") from exc
        for vertex in section_vertices:
            try:
                vertex["joints"] = tuple(
                    palette[index] if weight else 0
                    for index, weight in zip(
                        vertex.pop("joint_palette_indices"), vertex["weights"]
                    )
                )
            except IndexError as exc:
                raise RuntimeError("Vertex joint index exceeds its bone palette.") from exc
        vertex_base = len(vertices)
        vertices.extend(section_vertices)
        section_parts = []
        for descriptor_index in range(descriptor_count):
            if cursor + 60 > len(data):
                raise RuntimeError("Truncated skinned submesh descriptor.")
            words = struct.unpack_from(">15I", data, cursor)
            primitive, primitive_count, base, count, material = words[:5]
            first_index, descriptor_index_count, bone_count = words[12:15]
            if primitive not in (3, 4):
                raise RuntimeError(f"Unsupported skinned primitive {primitive}.")
            expected = primitive_count * 3 if primitive == 3 else primitive_count + 2
            if descriptor_index_count != expected:
                raise RuntimeError("Skinned descriptor index count is inconsistent.")
            local_indices = indices[first_index:first_index + expected]
            decoder = triangle_list if primitive == 3 else triangle_strip
            triangles, diagnostics = decoder(local_indices, count)
            triangles = [
                (a + base + vertex_base, b + base + vertex_base, c + base + vertex_base)
                for a, b, c in triangles
            ]
            part = {
                "submesh_index": len(mesh_parts),
                "section_index": section_index,
                "descriptor_index": descriptor_index,
                "material_index": material,
                "first_index": first_index,
                "index_count": expected,
                "triangles": triangles,
                "strip_diagnostics": diagnostics,
            }
            section_parts.append(part)
            mesh_parts.append(part)
            cursor += 60
            if cursor + bone_count * 4 > len(data):
                raise RuntimeError("Submesh bone list exceeds the file.")
            part["bone_ids"] = list(struct.unpack_from(">" + "I" * bone_count, data, cursor))
            cursor += bone_count * 4
        sections.append({
            "offset": section_offset,
            "vertex_count": vertex_count,
            "index_count": index_count,
            "index_offset": index_offset,
            "attribute16_offset": attribute_offset,
            "position_offset": position_offset,
            "palette_offset": palette_offset,
            "palette_ids": list(palette_ids),
            "submesh_count": descriptor_count,
        })
    return {
        "vertices": vertices,
        "mesh_parts": mesh_parts,
        "sections": sections,
        "end_offset": cursor,
    }


def find_external_character_motion(model_path: Path):
    """Locate and validate the separate motion files belonging to a character."""
    kb_root = next(
        (parent for parent in model_path.parents if parent.name.lower() == "kb"),
        None,
    )
    if kb_root is None:
        return None
    name = model_path.stem
    sequence_path = kb_root / "motionSequence" / name / name
    package_path = kb_root / "motionPackage" / name / "BigEndian" / name
    result = {
        "sequence_path": str(sequence_path) if sequence_path.is_file() else None,
        "package_path": str(package_path) if package_path.is_file() else None,
        "clip_count": 0,
        "package_entry_count": None,
        "decoded": False,
    }
    if sequence_path.is_file():
        motion = sequence_path.read_bytes()
        if len(motion) >= 0x88:
            count = be_u32(motion, 0x80)
            if 0 < count <= 100_000 and 0x84 + count * 4 <= len(motion):
                offsets = struct.unpack_from(">" + "I" * count, motion, 0x84)
                absolute = [0x80 + offset for offset in offsets]
                if absolute == sorted(absolute) and all(
                    0x84 + count * 4 <= offset <= len(motion)
                    for offset in absolute
                ):
                    # The table stores clip boundaries, including the EOF
                    # sentinel as its final entry.
                    result["clip_count"] = max(0, count - 1)
                    result["first_clip_offset"] = absolute[0]
                    result["last_clip_offset"] = absolute[-2] if len(absolute) > 1 else None
                    result["sequence_end_offset"] = absolute[-1]
                    first_clip = absolute[0]
                    if first_clip < len(motion):
                        bone_count = motion[first_clip]
                        bone_ids_offset = first_clip + bone_count * 2
                        bone_ids_end = bone_ids_offset + bone_count
                        if (
                            bone_count > 0
                            and bone_ids_end <= absolute[1]
                            and len(set(motion[bone_ids_offset:bone_ids_end]))
                                == bone_count
                        ):
                            # The first clip supplies the canonical transform
                            # order. DDM bone IDs/parents use another order.
                            result["skeleton_bone_ids"] = list(
                                motion[bone_ids_offset:bone_ids_end]
                            )
    if package_path.is_file():
        package = package_path.read_bytes()
        if len(package) >= 12 and package[:4] == b"\x00crg":
            result["package_entry_count"] = be_u32(package, 8)
    if not result["sequence_path"] and not result["package_path"]:
        return None
    return result


def analyze_skinned_file(path, data, out_dir, args, header):
    """Decode and export the observed character skin/skeleton DDM variant."""
    if getattr(args, "format", "glb") != "glb":
        raise UnsupportedDDMVariant(
            "skinned characters require GLB export; OBJ cannot store skins"
        )
    external_motion = find_external_character_motion(path)
    transform_bone_ids = (
        external_motion.get("skeleton_bone_ids")
        if external_motion else None
    )
    skeleton = decode_skinned_skeleton(data, transform_bone_ids)
    geometry = decode_skinned_geometry(data, header, skeleton)
    vertices = geometry["vertices"]
    mesh_parts = geometry["mesh_parts"]
    material_count = max(part["material_index"] for part in mesh_parts) + 1
    materials = parse_materials(data, header["offset"], material_count)
    if len(materials) != material_count:
        materials = [
            {
                "index": index,
                "name": f"material_{index}",
                "texture_references": [],
                "phong": None,
                "pbr_estimate": None,
            }
            for index in range(material_count)
        ]
    image_data = {}
    if not args.no_textures:
        with tempfile.TemporaryDirectory(prefix="ddm-glb-textures-") as temp:
            texture_output = Path(temp)
            materials = resolve_material_textures(
                materials, path, texture_output, args.texture_root,
            )
            for material in materials:
                for texture in material.get("textures", []):
                    output = texture.get("output")
                    if output:
                        image_data[output] = (texture_output / output).read_bytes()
                        texture["embedded_in_glb"] = texture["role"] in (
                            "diffuse", "normal",
                        )
    else:
        for material in materials:
            material["textures"] = []

    out_dir.mkdir(parents=True, exist_ok=True)
    mesh_path = out_dir / f"{path.stem}.glb"
    result = write_skinned_glb(
        mesh_path,
        vertices,
        mesh_parts,
        materials,
        skeleton,
        path.stem,
        args.scale,
        image_data=image_data,
        roughness=getattr(args, "roughness", 0.8),
    )
    report = {
        "file": str(path),
        "file_size": len(data),
        "version": be_u32(data, 4),
        "geometry_variant": "skinned_8_attribute_28_byte_vertex",
        "header": header,
        "sections": geometry["sections"],
        "vertex_count": len(vertices),
        "triangle_count": result["triangle_count"],
        "skeleton": {
            "transform_count": skeleton["transform_count"],
            "joint_count": skeleton["joint_count"],
            "joints": skeleton["joints"],
        },
        "materials": materials,
        "glb_export": result,
        "external_motion": external_motion,
        "animation_source": None,
        "animation_note": (
            "No animation stream is embedded in this DDM. External motionPackage/"
            "motionSequence formats require a separate decoder."
        ),
    }
    if not args.final:
        with (out_dir / "analysis.json").open("w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    print(f"\n[{path.name}]")
    print("  geometry variant : skinned character")
    print(f"  sections         : {len(geometry['sections'])}")
    print(f"  vertices         : {len(vertices)}")
    print(f"  triangles        : {result['triangle_count']}")
    if result["source_triangle_count"] != result["triangle_count"]:
        print(
            "  material variants: "
            f"{result['material_variant_count']} "
            f"({result['source_triangle_count'] - result['triangle_count']} "
            "overlapping variant faces consolidated)"
        )
    print(f"  joints           : {result['joint_count']}")
    print(f"  materials        : {len(materials)}")
    if external_motion and external_motion["clip_count"]:
        print(
            "  animations       : 0 embedded; "
            f"{external_motion['clip_count']} external clips detected "
            "(motion decoder pending)"
        )
    else:
        print("  animations       : 0 embedded")
    print(f"  output           : {mesh_path}")
    return report


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


def write_obj(
    path: Path,
    vertices,
    mesh_parts,
    materials,
    object_name: str,
    scale: float,
):
    materials_by_index = {material["index"]: material for material in materials}
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Experimental DDM geometry decoder\n")
        f.write("# Primitive codes: 3 = triangle lists, 4 = triangle strips.\n")
        f.write(f"# Source positions multiplied by {scale:.9g}.\n")
        f.write("mtllib materials.mtl\n")
        f.write(f"o {object_name}\n")

        for v in vertices:
            x, y, z = v["position"]
            x, y, z = x * scale, y * scale, z * scale
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
            phong = material.get("phong")
            if phong:
                diffuse_color = phong["diffuse"]
                ambient_color = phong["ambient"]
                specular_color = phong["specular"]
                shininess = phong["shininess"]
            else:
                diffuse_color = (r, g, b)
                ambient_color = (0.05, 0.05, 0.05)
                specular_color = (0.15, 0.15, 0.15)
                shininess = 32.0

            source_colors = (
                diffuse_color,
                ambient_color,
                specular_color,
            )
            diffuse_color, ambient_color, specular_color = (
                tuple(min(1.0, max(0.0, value)) for value in color)
                for color in source_colors
            )

            f.write(f"newmtl {material_export_name(material)}\n")
            f.write(f'# DDM material index: {material_index}\n')
            if phong:
                f.write(
                    f'# Legacy Phong parameters decoded at '
                    f'0x{phong["offset"]:X}\n'
                )
                pbr = material["pbr_estimate"]
                f.write(
                    "# PBR roughness is not stored directly; estimate from "
                    f'Ns: {pbr["roughness"]:.6g}\n'
                )
            else:
                f.write("# Phong parameters not decoded; using fallbacks\n")
            f.write("# No metallic parameter has been identified in DDM\n")
            if source_colors != (
                diffuse_color,
                ambient_color,
                specular_color,
            ):
                f.write(
                    "# Source colors outside the MTL range [0, 1] were "
                    "clamped for MTL compatibility\n"
                )
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
            f.write("Kd " + " ".join(f"{v:.6g}" for v in diffuse_color) + "\n")
            f.write("Ka " + " ".join(f"{v:.6g}" for v in ambient_color) + "\n")
            f.write("Ks " + " ".join(f"{v:.6g}" for v in specular_color) + "\n")
            f.write(f"Ns {shininess:.6g}\n")
            if diffuse:
                f.write(f'map_Kd {diffuse["output"]}\n')
            if specular:
                f.write(f'map_Ks {specular["output"]}\n')
            if normal:
                # `norm` is widely supported as a tangent-space extension;
                # map_Bump keeps compatibility with simpler OBJ importers.
                f.write(f'norm {normal["output"]}\n')
                f.write(f'map_Bump -bm 1.0 {normal["output"]}\n')
            f.write("illum 2\n\n")


def write_point_obj(
    path: Path,
    vertices,
    object_name: str,
    scale: float,
):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# DDM position stream only\n")
        f.write(f"o {object_name}_points\n")
        for v in vertices:
            x, y, z = v["position"]
            x, y, z = x * scale, y * scale, z * scale
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


def analyze_file(
    path: Path,
    out_root: Path,
    args,
    relative_path: Path | None = None,
):
    export_format = getattr(args, "format", "glb")
    image_data = {}
    data = path.read_bytes()

    if len(data) < 8 or data[:4] != MAGIC:
        print(f"[SKIP] {path.name}: not a DDM v3-like file")
        return

    version = be_u32(data, 4)
    output_key = (
        Path(path.stem)
        if relative_path is None
        else relative_path.parent / path.stem
    )
    out_dir = out_root / output_key

    periodic = find_periodic_marker_run(data)

    overrides = any(value is not None for value in (
        args.vertex_count, args.position_offset, args.index_offset, args.index_count,
    ))
    sections = [] if overrides else find_map_geometry_sections(data)
    skinned_header = None if overrides or sections else find_skinned_geometry_header(data)
    if skinned_header:
        return analyze_skinned_file(path, data, out_dir, args, skinned_header)
    if sections:
        vertices, attributes, indices, submeshes = [], [], [], []
        for section_index, section in enumerate(sections):
            vertex_base, index_base = len(vertices), len(indices)
            section_vertices = decode_vertices24(
                data, section["position_offset"], section["vertex_count"],
            )
            section_attributes = decode_vertices16(
                data, section["attribute16_offset"], section["vertex_count"],
            )
            for vertex, attribute in zip(section_vertices, section_attributes):
                vertex["index"] += vertex_base
                vertex.update({
                    "normal": attribute["normal"],
                    "tangent": attribute["tangent"],
                    "bitangent": attribute["bitangent"],
                    "attribute_marker": attribute["marker"],
                })
            vertices.extend(section_vertices)
            attributes.extend(section_attributes)
            indices.extend(decode_u16_buffer(
                data, section["index_offset"], section["index_count"],
            ))
            for sm in section["submeshes"]:
                submeshes.append(sm | {
                    "section_index": section_index,
                    "base_vertex": sm["base_vertex"] + vertex_base,
                    "first_index_field_0x34": sm["first_index_field_0x34"] + index_base,
                })
        header = sections[0]
        vertex_count, index_count = len(vertices), len(indices)
        position_offset = header["position_offset"]
        position_end = sections[-1]["position_end"]
        index_offset = header["index_offset"]
        attribute16_offset = header["attribute16_offset"]
        detected_attribute16_offset = attribute16_offset
        score = min(section["position_stream_score"] for section in sections)
        position_diag = {"sections": [s["position_diagnostics"] for s in sections]}
    else:
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
                "phong": None,
                "pbr_estimate": None,
            }
            for material_index in range(material_count)
        ]
    if not args.no_textures:
        if export_format == "glb":
            # GLB embeds PNGs; keep intermediate conversions outside the output.
            with tempfile.TemporaryDirectory(prefix="ddm-glb-textures-") as temp:
                texture_output = Path(temp)
                materials = resolve_material_textures(
                    materials, path, texture_output, args.texture_root,
                )
                for material in materials:
                    for texture in material.get("textures", []):
                        output = texture.get("output")
                        if output:
                            image_data[output] = (texture_output / output).read_bytes()
                            texture["embedded_in_glb"] = texture["role"] in ("diffuse", "normal")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            materials = resolve_material_textures(
                materials, path, out_dir, args.texture_root,
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
        "geometry_sections": sections,
        "vertex_count": vertex_count,
        "position_offset": position_offset,
        "position_stride": POSITION_STRIDE,
        "streams_are_contiguous": len(sections) <= 1,
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
        "source_bbox": bbox,
        "source_max_radius_from_origin": max_radius,
        "export_scale": args.scale,
        "exported_bbox": [
            [lower * args.scale, upper * args.scale]
            for lower, upper in bbox
        ],
        "exported_max_radius_from_origin": max_radius * args.scale,
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

    mesh_path = out_dir / f"{path.stem}.{export_format}"
    legacy_mesh_path = out_dir / "mesh.obj"
    optional_outputs = (
        out_dir / "positions_only.obj",
        out_dir / "vertices.csv",
        out_dir / "indices.csv",
        out_dir / "analysis.json",
    )

    # Remove files generated by an earlier diagnostic export when producing a
    # final asset, as well as the former generic mesh filename.
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_outputs = optional_outputs if args.final else ()
    if legacy_mesh_path != mesh_path:
        stale_outputs = (*stale_outputs, legacy_mesh_path)
    for stale_path in stale_outputs:
        stale_path.unlink(missing_ok=True)

    if not args.final:
        # The position cloud and CSV streams are reverse-engineering aids.
        write_point_obj(
            out_dir / "positions_only.obj",
            vertices,
            path.stem,
            args.scale,
        )
        write_vertices_csv(
            out_dir / "vertices.csv",
            vertices,
        )
        write_indices(out_dir / "indices.csv", indices)

    if export_format == "glb":
        object_mode = getattr(args, "object_mode", "auto")
        if object_mode == "auto":
            object_mode = "connected" if sections else "single"
        report["glb_export"] = write_glb(
            mesh_path, vertices, mesh_parts, materials, path.stem, args.scale,
            mode=object_mode, image_data=image_data,
            roughness=getattr(args, "roughness", 0.8),
        )
    else:
        write_mtl(out_dir / "materials.mtl", materials)
        write_obj(
            mesh_path, vertices, mesh_parts, materials, path.stem, args.scale,
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

    if not args.final:
        with (out_dir / "analysis.json").open(
            "w", encoding="utf-8"
        ) as f:
            json.dump(report, f, indent=2)

    print(f"\n[{path.name}]")
    print(f"  version          : {version}")
    if sections:
        print(f"  geometry sections: {len(sections)}")
    print(f"  vertices         : {vertex_count}")
    print(f"  position stream  : 0x{position_offset:X}")
    print(f"  position end     : 0x{position_end:X}")
    print(f"  attribute stream : 0x{attribute16_offset:X}")
    print(f"  position score   : {score:.3f}")
    print(f"  source radius    : {max_radius:.6f}")
    print(f"  export scale     : {args.scale:.9g}")
    print(f"  exported radius  : {max_radius * args.scale:.6f}")

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
        phong = material.get("phong")
        phong_summary = (
            f' Ks={phong["specular"]} Ns={phong["shininess"]:.6g}'
            if phong
            else " Phong=unresolved"
        )
        print(
            f'    #{material["index"]}: {material["name"]}'
            + phong_summary
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

    if "glb_export" in report:
        glb = report["glb_export"]
        print(f"  GLB objects      : {glb['object_count']} ({glb['separation']})")
        print(f"  GLB meshes       : {glb['mesh_count']}")
        print(f"  GLB triangles    : {glb['triangle_count']}")
        print(
            "  removed overlays : "
            f"{glb['removed_normal_only_surface_passes']} normal-only, "
            f"{glb['removed_exact_duplicate_faces']} exact duplicates"
        )
    print(f"  output           : {mesh_path}")


def parse_int(value: str):
    return int(value, 0)


def parse_bool(value: str):
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Expected a boolean value, got {value!r}."
    )


def iter_input_files(path: Path, recursive: bool = False):
    if path.is_file():
        yield path
    elif path.is_dir():
        candidates = path.rglob("*") if recursive else path.iterdir()
        for p in sorted(candidates):
            if p.is_file():
                yield p
    else:
        raise FileNotFoundError(path)


def output_directory_for(path: Path, out_root: Path, relative_path=None):
    output_key = (
        Path(path.stem)
        if relative_path is None
        else relative_path.parent / path.stem
    )
    return out_root / output_key


def prune_empty_output_directories(path: Path, out_root: Path):
    """Remove only empty directories below the configured output root."""
    boundary = out_root.resolve(strict=False)
    current = path
    while current.resolve(strict=False) != boundary:
        try:
            current.rmdir()
        except (FileNotFoundError, OSError):
            break
        current = current.parent


def main():
    ap = argparse.ArgumentParser(
        description="Experimental PS3 DDM to GLB or OBJ/MTL converter"
    )
    ap.add_argument("input", type=Path, help="DDM file or directory")
    ap.add_argument("output", type=Path, help="Output directory")
    ap.add_argument("--format", choices=("glb", "obj"), default="glb",
                    help="Export format (default: self-contained GLB).")
    ap.add_argument("--object-mode", choices=("auto", "connected", "submeshes", "single"),
                    default="auto", help=(
                        "GLB object separation: auto splits map geometry into connected "
                        "components and keeps character models together. Components "
                        "are reconstructed, not original DDM authoring instances."))
    ap.add_argument(
        "--roughness",
        type=float,
        default=0.8,
        help=(
            "GLB PBR roughness (default: 0.8, suitable for dry map surfaces). "
            "Use a negative value to derive it from legacy Phong shininess."
        ),
    )
    ap.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=(
            "Scan input directories recursively and preserve their relative "
            "layout below the output directory."
        ),
    )
    ap.add_argument(
        "--final",
        nargs="?",
        const=True,
        default=False,
        type=parse_bool,
        metavar="BOOL",
        help=(
            "Generate only the GLB, or OBJ/MTL/textures with --format obj. "
            "Diagnostic OBJ/CSV/JSON files are omitted. Accepts true/false; "
            "using --final without a value means true."
        ),
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_EXPORT_SCALE,
        help=(
            "Multiplier applied to exported positions. The default "
            f"({DEFAULT_EXPORT_SCALE}) converts the observed centimeter-like "
            "DDM coordinates to meters."
        ),
    )

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
    if not math.isfinite(args.scale) or args.scale <= 0.0:
        ap.error("--scale must be a finite number greater than zero.")
    if not math.isfinite(args.roughness) or args.roughness > 1.0:
        ap.error("--roughness must be at most 1.0 (negative means Phong-derived).")
    if args.roughness < 0.0:
        args.roughness = None
    args.output.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    unsupported = 0
    failed = 0
    input_is_directory = args.input.is_dir()
    input_files = list(iter_input_files(args.input, args.recursive))

    for path in input_files:
        try:
            data = path.read_bytes()
            if len(data) < 8 or data[:4] != MAGIC:
                if args.debug:
                    print(f"[SKIP] {path}: magic mismatch")
                skipped += 1
                continue

            relative_path = (
                path.relative_to(args.input)
                if input_is_directory
                else None
            )
            try:
                analyze_file(
                    path,
                    args.output,
                    args,
                    relative_path=relative_path,
                )
            except UnsupportedDDMVariant as exc:
                print(f"[UNSUPPORTED] {path}: {exc}")
                unsupported += 1
                prune_empty_output_directories(
                    output_directory_for(path, args.output, relative_path),
                    args.output,
                )
                continue
            processed += 1

        except Exception as exc:
            print(f"[ERROR] {path}: {exc}")
            failed += 1
            relative_path = (
                path.relative_to(args.input)
                if input_is_directory
                else None
            )
            prune_empty_output_directories(
                output_directory_for(path, args.output, relative_path),
                args.output,
            )
            if args.debug:
                raise

    print(
        f"\nSummary: {processed} decoded, {unsupported} unsupported, {failed} failed, "
        f"{skipped} non-DDM files skipped."
    )
    if processed == 0:
        if unsupported:
            print("No supported geometry decoded; unsupported DDM files were skipped.")
        else:
            print("No DDM file decoded.")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
