"""Skinned character DDM decoding: skeleton, skin and export."""
from __future__ import annotations

import json
import math
import struct
import tempfile
from pathlib import Path

from .binary import (
    ATTRIBUTE_STRIDE, be_u32, decode_half2_be, decode_u16_buffer,
    decode_vertices16, finite3,
)
from .geometry import triangle_list, triangle_strip
from .materials import (
    apply_matcap_pbr_estimates, export_texture_payload, material_mode_of,
    parse_materials, resolve_material_textures, uses_godot_materials,
)

try:
    from ..blend_mask import DEFAULT_BLEND_RADIUS
    from ..glb_export import write_skinned_glb
    from ..godot_export import write_godot_material_assets
    from ..motion_decode import (
        decode_character_animations, discover_character_motion,
    )
except ImportError:
    from blend_mask import DEFAULT_BLEND_RADIUS
    from glb_export import write_skinned_glb
    from godot_export import write_godot_material_assets
    from motion_decode import (
        decode_character_animations, discover_character_motion,
    )


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


def analyze_skinned_file(path, data, out_dir, args, header):
    """Decode and export the observed character skin/skeleton DDM variant."""
    external_motion = discover_character_motion(path)
    transform_bone_ids = (
        external_motion.get("skeleton_bone_ids")
        if external_motion else None
    )
    skeleton = decode_skinned_skeleton(data, transform_bone_ids)
    animations = decode_character_animations(
        external_motion, skeleton, getattr(args, "animation_clips", None),
        getattr(args, "experimental_root_motion", False),
        getattr(args, "experimental_rotation_joints", None),
        getattr(args, "experimental_rotation_units", "degrees"),
    )
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
            if material_mode_of(args) == "pbr":
                apply_matcap_pbr_estimates(materials)
            for material in materials:
                for texture in material.get("textures", []):
                    output = texture.get("output")
                    if output:
                        image_data[output] = export_texture_payload(
                            texture_output, texture,
                        )
                        texture["embedded_in_glb"] = texture["role"] in (
                            "diffuse", "normal",
                        )
    else:
        for material in materials:
            material["textures"] = []

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if getattr(args, "experimental_rotation_joints", None):
        suffix = "_motion_experimental"
        units = getattr(args, "experimental_rotation_units", "degrees")
        if units != "degrees":
            suffix += f"_{units}"
    mesh_path = out_dir / f"{path.stem}{suffix}.glb"
    result = write_skinned_glb(
        mesh_path,
        vertices,
        mesh_parts,
        materials,
        skeleton,
        path.stem,
        args.scale,
        image_data=image_data,
        roughness=getattr(args, "roughness", None),
        animations=animations,
    )
    godot_materials = None
    if uses_godot_materials(args):
        godot_materials = write_godot_material_assets(
            out_dir, materials, image_data,
            vertices=vertices,
            mesh_parts=mesh_parts,
            scale=args.scale,
            radius=getattr(args, "blend_radius", DEFAULT_BLEND_RADIUS),
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
        "godot_materials": godot_materials,
        "external_motion": external_motion,
        "animation_source": (
            external_motion.get("sequence_path")
            if animations and external_motion else None
        ),
        "animations": animations,
        "animation_note": (
            "Only the selected clips' verified root translation and heading are exported; joint "
            "rotations remain unresolved and omitted."
            if animations else
            "External scalar curves are decoded and validated, but their association "
            "with joint transforms remains unresolved. No guessed animation is exported."
            if external_motion else "No matching external motion resource was found."
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
    if animations:
        print(
            f"  animations       : {len(animations)} exported; "
            f"{external_motion['clip_count']} external clips detected"
        )
    elif external_motion and external_motion["clip_count"]:
        print(
            "  animations       : 0 exported; "
            f"{external_motion['clip_count']} external clips detected "
            "(scalar curves decoded; rig binding pending)"
        )
    else:
        print("  animations       : 0 embedded")
    print(f"  output           : {mesh_path}")
    if godot_materials:
        print(f"  Godot shader     : {out_dir / godot_materials['shader']}")
        print(f"  Godot bindings   : {out_dir / godot_materials['manifest']}")
    return report
