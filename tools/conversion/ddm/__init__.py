"""DDM reverse-engineering and glTF/GLB conversion package.

The package splits the former monolithic ``ddm_to_3d`` module into focused
submodules:

- :mod:`binary` reads primitives and packed vertex attributes;
- :mod:`materials` parses materials and resolves textures/PBR estimates;
- :mod:`geometry` decodes topology, submeshes and map sections;
- :mod:`skinned` decodes skinned characters (skeleton + skin);
- :mod:`scene` orchestrates model/scene decoding and GLB export;
- :mod:`cli` is the command-line entry point.

All public names are re-exported here so ``ddm_to_3d`` and external callers keep
working unchanged.
"""

from __future__ import annotations

from .binary import (
    ATTRIBUTE_STRIDE,
    KNOWN_DDM_VERSIONS,
    MAGIC,
    POSITION_STRIDE,
    UnsupportedDDMVariant,
    be_f32,
    be_u16,
    be_u32,
    decode_half2_be,
    decode_packed_11_11_10,
    decode_snorm,
    decode_u16_buffer,
    decode_vertices16,
    decode_vertices24,
    ddm_variant_name,
    finite3,
    find_periodic_marker_run,
    score_position_stream,
    vec_cross,
    vec_dot,
    vec_len,
    vec_normalize,
    vec_sub,
)
from .materials import (
    DEFAULT_EXPORT_SCALE,
    SAFE_NAME_RE,
    apply_matcap_pbr_estimates,
    apply_render_state_pbr_policy,
    classify_material_textures,
    convert_xet_texture,
    decode_material_render_state,
    export_texture_payload,
    extract_length_prefixed_strings,
    find_phong_parameters,
    image_content_stats,
    material_mode_of,
    parse_materials,
    phong_to_pbr_estimate,
    resolve_material_textures,
    resolve_texture_path,
    stabilize_map_pbr_estimates,
    texture_candidate_rank,
    texture_file_index,
    texture_storage_names,
    texture_suffix_role,
    uses_godot_materials,
)
from .geometry import (
    SUBMESH_SIGNATURE,
    build_mesh_parts,
    find_geometry_header,
    find_map_geometry_sections,
    find_submesh_descriptors,
    normal_alignment_stats,
    quaternion_rotate,
    topology_stats,
    triangle_list,
    triangle_strip,
    validate_obb,
    write_indices,
    write_vertices_csv,
)
from .skinned import (
    analyze_skinned_file,
    decode_skinned_geometry,
    decode_skinned_skeleton,
    find_skinned_geometry_header,
)
from .scene import analyze_file, output_key_for
from .cli import (
    iter_input_files,
    main,
    output_directory_for,
    parse_bool,
    parse_int,
    prune_empty_output_directories,
)

__all__ = [
    "ATTRIBUTE_STRIDE", "KNOWN_DDM_VERSIONS", "MAGIC", "POSITION_STRIDE",
    "UnsupportedDDMVariant",
    "DEFAULT_EXPORT_SCALE", "SAFE_NAME_RE", "SUBMESH_SIGNATURE",
    "analyze_file", "analyze_skinned_file", "apply_matcap_pbr_estimates",
    "be_f32", "be_u16", "be_u32", "build_mesh_parts",
    "classify_material_textures", "convert_xet_texture",
    "decode_material_render_state", "apply_render_state_pbr_policy",
    "decode_half2_be", "decode_packed_11_11_10", "decode_skinned_geometry",
    "decode_skinned_skeleton", "decode_snorm", "decode_u16_buffer",
    "decode_vertices16", "decode_vertices24", "ddm_variant_name",
    "export_texture_payload",
    "extract_length_prefixed_strings", "find_geometry_header",
    "find_map_geometry_sections", "find_periodic_marker_run",
    "find_phong_parameters", "find_skinned_geometry_header",
    "find_submesh_descriptors", "finite3", "image_content_stats",
    "iter_input_files", "main", "normal_alignment_stats",
    "material_mode_of", "output_directory_for", "output_key_for", "parse_bool", "parse_int",
    "parse_materials",
    "phong_to_pbr_estimate", "prune_empty_output_directories",
    "quaternion_rotate", "resolve_material_textures", "resolve_texture_path",
    "score_position_stream", "stabilize_map_pbr_estimates",
    "texture_candidate_rank", "texture_file_index", "texture_storage_names",
    "texture_suffix_role",
    "topology_stats", "triangle_list", "triangle_strip", "uses_godot_materials",
    "validate_obb",
    "vec_cross", "vec_dot", "vec_len", "vec_normalize", "vec_sub",
    "write_indices", "write_vertices_csv",
]
