"""Scene/model DDM decoding and GLB export orchestration."""
from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path

from .binary import (
    ATTRIBUTE_STRIDE, MAGIC, POSITION_STRIDE, UnsupportedDDMVariant, be_f32,
    be_u32, decode_u16_buffer, decode_vertices16, decode_vertices24,
    find_periodic_marker_run, score_position_stream, vec_len,
)
from .geometry import (
    build_mesh_parts, find_geometry_header, find_map_geometry_sections,
    find_submesh_descriptors, normal_alignment_stats, topology_stats,
    validate_obb, write_indices, write_vertices_csv,
)
from .materials import (
    apply_matcap_pbr_estimates, export_texture_payload, material_mode_of,
    parse_materials, resolve_material_textures, stabilize_map_pbr_estimates,
    uses_godot_materials,
)
from .skinned import analyze_skinned_file, find_skinned_geometry_header

try:
    from ..blend_mask import DEFAULT_BLEND_RADIUS
    from ..glb_export import write_glb
    from ..godot_export import _fold_multipass_details, write_godot_material_assets
except ImportError:
    from blend_mask import DEFAULT_BLEND_RADIUS
    from glb_export import write_glb
    from godot_export import _fold_multipass_details, write_godot_material_assets


def output_key_for(path: Path, relative_path: Path | None = None) -> Path:
    """Return a stable model directory without duplicating named containers.

    Extracted assets commonly use ``ins107/ins107`` or ``map101/map101``.
    During recursive conversion the containing directory already identifies
    the model, so appending the file stem again would create a redundant
    ``ins107/ins107`` output directory.
    """
    if relative_path is None:
        return Path(path.stem)
    parent = relative_path.parent
    if parent.name.casefold() == path.stem.casefold():
        return parent
    return parent / path.stem


def analyze_file(
    path: Path,
    out_root: Path,
    args,
    relative_path: Path | None = None,
):
    image_data = {}
    data = path.read_bytes()

    if len(data) < 8 or data[:4] != MAGIC:
        print(f"[SKIP] {path.name}: not a DDM v3-like file")
        return

    version = be_u32(data, 4)
    output_key = output_key_for(path, relative_path)
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
            raise UnsupportedDDMVariant(
                "could not auto-detect the vertex count; pass --vertex-count "
                "(or --position-offset/--index-offset) for this layout",
                version=version,
            )

        if args.position_offset is not None:
            position_offset = args.position_offset
        elif periodic:
            position_offset = periodic["position_offset"]
        else:
            raise UnsupportedDDMVariant(
                "could not auto-detect the position stream; pass "
                "--position-offset for this layout",
                version=version,
            )

        score, position_diag = score_position_stream(
            data, position_offset, vertex_count
        )

        if score < 0:
            raise UnsupportedDDMVariant(
                f"position stream candidate 0x{position_offset:X} is invalid",
                version=version,
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
            raise UnsupportedDDMVariant(
                "could not locate the index buffer; pass --index-offset and "
                "--index-count for this layout",
                version=version,
            )

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
            raise UnsupportedDDMVariant(
                "no submesh descriptor found for this layout",
                version=version,
            )

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
    if sections:
        stabilize_map_pbr_estimates(materials)
    if not args.no_textures:
        # GLB embeds PNGs; keep intermediate conversions outside the output.
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
                        texture["embedded_in_glb"] = texture["role"] in ("diffuse", "normal")
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

    mesh_path = out_dir / f"{path.stem}.glb"
    optional_outputs = (
        out_dir / "vertices.csv",
        out_dir / "indices.csv",
        out_dir / "analysis.json",
    )

    # Remove files generated by an earlier diagnostic export when producing a
    # final asset, as well as the former OBJ outputs.
    out_dir.mkdir(parents=True, exist_ok=True)
    stale_outputs = optional_outputs if args.final else ()
    stale_outputs = (*stale_outputs, out_dir / "mesh.obj",
                     out_dir / "positions_only.obj", out_dir / "materials.mtl")
    for stale_path in stale_outputs:
        stale_path.unlink(missing_ok=True)

    if not args.final:
        # The CSV streams are reverse-engineering aids.
        write_vertices_csv(
            out_dir / "vertices.csv",
            vertices,
        )
        write_indices(out_dir / "indices.csv", indices)

    is_instance_asset = any(
        component.casefold() == "instance" for component in path.parts[:-1]
    )
    object_mode = getattr(args, "object_mode", "auto")
    if object_mode == "auto":
        # DDM instance assets are complete reusable props. Their disconnected
        # cards (especially foliage) are parts of one model, not authoring
        # instances to reconstruct as separate GLB nodes.
        object_mode = "single" if is_instance_asset else (
            "connected" if sections else "single"
        )
    detail_folds = []
    excluded_materials = set()
    if uses_godot_materials(args):
        detail_folds = _fold_multipass_details(materials, mesh_parts, vertices)
        excluded_materials = {
            fold["overlay_material_index"] for fold in detail_folds
        }

    report["glb_export"] = write_glb(
        mesh_path, vertices, mesh_parts, materials, path.stem, args.scale,
        mode=object_mode, image_data=image_data,
        roughness=getattr(args, "roughness", None),
        excluded_materials=excluded_materials,
        preserve_source_origin=is_instance_asset,
    )
    if uses_godot_materials(args):
        report["godot_materials"] = write_godot_material_assets(
            out_dir, materials, image_data,
            vertices=vertices,
            mesh_parts=mesh_parts,
            scale=args.scale,
            radius=getattr(args, "blend_radius", DEFAULT_BLEND_RADIUS),
            detail_folds=detail_folds,
            asset_kind="instance" if is_instance_asset else "model",
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
            f"{glb['removed_exact_duplicate_faces']} exact duplicates, "
            f"{glb.get('removed_multipass_layer_faces', 0)} multipass layers"
        )
    if report.get("godot_materials"):
        godot = report["godot_materials"]
        print(f"  Godot shader     : {godot['shader']}")
        print(f"  Godot bindings   : {out_dir / godot['manifest']}")
        if godot.get("detail_fold_count"):
            print(f"  Godot detail maps: {godot['detail_fold_count']} multipass overlay(s) folded")
    print(f"  output           : {mesh_path}")
