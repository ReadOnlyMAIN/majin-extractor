"""Godot 4 material assets for reconstructed DDM shaders."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

try:
    from .blend_mask import (
        BLEND_MAP_SIZE,
        DEFAULT_BLEND_RADIUS,
        MAX_BLEND_MATERIALS,
        build_blend_map,
        select_material_slots,
        write_png,
    )
    from .ddm.materials import is_foliage_render_state
except ImportError:
    from blend_mask import (
        BLEND_MAP_SIZE,
        DEFAULT_BLEND_RADIUS,
        MAX_BLEND_MATERIALS,
        build_blend_map,
        select_material_slots,
        write_png,
    )
    from ddm.materials import is_foliage_render_state

GODOT_SHADER_COMMON = """
// Visual approximation reconstructed from KbBase P31/P33 sampler bindings.
// RSX disassembly shows that EnvSphere RGB is used as signed vector data, not
// simply added as color. Strength and mask polarity remain exposed so this
// portable shader is not mistaken for a byte-exact translation.
//
// Fallback multi-material blending for layouts without source paint weights.
// Map101 instead uses its decoded vertex alpha and leaves use_blend disabled.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform sampler2D secondary_texture : source_color, hint_default_white;
uniform sampler2D utility_texture : hint_default_white;
uniform sampler2D normal_texture : hint_normal;
uniform sampler2D secondary_normal_texture : hint_normal;
uniform sampler2D env_sphere_texture : source_color, hint_default_black, repeat_disable;

uniform sampler2D blend_map : hint_default_white, repeat_disable;
uniform sampler2D blend_base_texture_1 : source_color, hint_default_white;
uniform sampler2D blend_base_texture_2 : source_color, hint_default_white;
uniform sampler2D blend_base_texture_3 : source_color, hint_default_white;

// Detail layer: several map materials are drawn once as a base surface and
// again as a detail overlay on exactly coincident geometry (for example the
// observed gake102__base / gake102__multi terrain). The exporter folds the
// overlay's textures into this second layer so the base and detail texture
// sets can be combined in one draw instead of z-fighting.
uniform sampler2D detail_texture : source_color, hint_default_white;
uniform sampler2D detail_normal_texture : hint_normal;
uniform sampler2D detail_mask : hint_default_white, repeat_disable;
uniform bool use_detail = false;
uniform bool use_detail_mask = false;
uniform bool use_detail_normal_texture = false;
uniform float detail_strength : hint_range(0.0, 1.0, 0.01) = 0.5;

uniform bool use_utility_texture = false;
uniform bool use_normal_texture = false;
uniform bool use_secondary_texture = false;
uniform bool use_secondary_normal_texture = false;
uniform bool use_env_sphere_texture = false;
uniform bool invert_utility = false;
uniform bool use_blend = false;
uniform bool use_vertex_alpha = false;
uniform float reflection_strength : hint_range(0.0, 2.0, 0.01) = 1.0;
uniform float roughness : hint_range(0.0, 1.0, 0.01);
uniform float specular : hint_range(0.0, 1.0, 0.01) = 0.5;
uniform vec4 albedo_modulate : source_color = vec4(1.0);

vec4 blend_base(vec2 uv, vec4 weights) {
    vec4 base = texture(base_texture, uv) * weights.r;
    base += texture(blend_base_texture_1, uv) * weights.g;
    base += texture(blend_base_texture_2, uv) * weights.b;
    base += texture(blend_base_texture_3, uv) * weights.a;
    return base;
}

void fragment() {
    vec4 weights = vec4(1.0, 0.0, 0.0, 0.0);
    if (use_blend) {
        weights = texture(blend_map, UV);
        float total = weights.r + weights.g + weights.b + weights.a;
        // Guard against an all-zero texel (unmapped UV): fall back to the
        // owner material so the surface never turns black.
        weights = total > 1e-5
            ? weights / total
            : vec4(1.0, 0.0, 0.0, 0.0);
    }
    vec4 base = use_blend ? blend_base(UV, weights) : texture(base_texture, UV);
    if (use_secondary_texture) {
        base = mix(base, texture(secondary_texture, UV), COLOR.a);
    }

    // Fold a coincident detail pass into the base draw. Its original vertex
    // alpha has been transferred to the host geometry, so COLOR.a is the
    // source-authored paint mask rather than an invented constant blend.
    if (use_detail) {
        float mix_amount = COLOR.a * detail_strength;
        if (use_detail_mask) {
            mix_amount *= texture(detail_mask, UV).r;
        }
        vec4 detail = texture(detail_texture, UV);
        base = mix(base, detail, clamp(mix_amount, 0.0, 1.0));
    }

    vec3 view_normal = normalize(NORMAL);

    if (use_normal_texture || use_secondary_normal_texture || use_detail_normal_texture) {
        vec3 sampled_normal = use_normal_texture
            ? texture(normal_texture, UV).rgb
            : vec3(0.5, 0.5, 1.0);
        if (use_secondary_normal_texture) {
            sampled_normal = mix(
                sampled_normal,
                texture(secondary_normal_texture, UV).rgb,
                COLOR.a
            );
        }
        if (use_detail_normal_texture) {
            float mix_amount = COLOR.a * detail_strength;
            if (use_detail_mask) {
                mix_amount *= texture(detail_mask, UV).r;
            }
            sampled_normal = mix(sampled_normal, texture(detail_normal_texture, UV).rgb,
                                 clamp(mix_amount, 0.0, 1.0));
        }
        NORMAL_MAP = sampled_normal;
        vec3 tangent_normal = sampled_normal * 2.0 - 1.0;
        view_normal = normalize(
            TANGENT * tangent_normal.x
            + BINORMAL * tangent_normal.y
            + NORMAL * tangent_normal.z
        );
    }

    float utility = use_utility_texture ? texture(utility_texture, UV).r : 1.0;
    if (invert_utility) {
        utility = 1.0 - utility;
    }

    vec3 reconstructed = base.rgb;
    if (use_env_sphere_texture) {
        vec2 sphere_uv = view_normal.xy * vec2(0.5, -0.5) + vec2(0.5);
        vec3 reflection = texture(env_sphere_texture, sphere_uv).rgb;
        reconstructed += reflection * utility * reflection_strength;
    }

    ALBEDO = reconstructed * COLOR.rgb * albedo_modulate.rgb;
#if MATERIAL_ALPHA_MODE == 1
    ALPHA = base.a * COLOR.a;
    ALPHA_SCISSOR_THRESHOLD = 0.5;
#elif MATERIAL_ALPHA_MODE == 2
    ALPHA = base.a * (use_vertex_alpha ? COLOR.a : 1.0);
#endif
    METALLIC = 0.0;
    ROUGHNESS = roughness;
    SPECULAR = specular;
}
"""

GODOT_CUSTOM_SHADER = """shader_type spatial;
#define MATERIAL_ALPHA_MODE 0
#include "majin_material_common.gdshaderinc"
"""

GODOT_FOLIAGE_SHADER = """shader_type spatial;
render_mode cull_disabled;

// Thin double-sided cutout cards. The source foliage variant only carries an
// albedo/opacity texture; its generic Phong block is not representative of the
// original leaf lighting. Flip the geometric normal on back faces so both
// visible sides react to the sun from the correct direction.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform vec4 albedo_modulate : source_color = vec4(1.0);

void fragment() {
\tvec4 base = texture(base_texture, UV);
\tNORMAL = FRONT_FACING ? NORMAL : -NORMAL;
\tALBEDO = base.rgb * COLOR.rgb * albedo_modulate.rgb;
\tALPHA = base.a * COLOR.a;
\tALPHA_SCISSOR_THRESHOLD = 0.5;
\tMETALLIC = 0.0;
\tROUGHNESS = 1.0;
\tSPECULAR = 0.0;
}
"""


ROLE_TO_UNIFORM = {
    "diffuse": "base_texture",
    "normal": "normal_texture",
    "utility_mask": "utility_texture",
    "matcap": "env_sphere_texture",
    # Detail-layer roles, used when a coincident multipass overlay is folded
    # into a base material instead of being exported as a z-fighting duplicate.
    "detail_diffuse": "detail_texture",
    "detail_normal": "detail_normal_texture",
    "detail_mask": "detail_mask",
}

# Stable Godot project location for the reusable utility assets. The exporter
# references the shader through this path so materials never embed a copy, and
# ``godot/utility/`` only has to be installed once.
GODOT_UTILITY_DIR = "res://majin_utility"
CUSTOM_SHADER_FILE_NAME = "majin_multitexture.gdshader"
FOLIAGE_SHADER_FILE_NAME = "majin_foliage.gdshader"
SHADER_COMMON_FILE_NAME = "majin_material_common.gdshaderinc"
ASSIGN_SCRIPT_FILE_NAME = "assign_materials.gd"
IMPORT_INSTANCE_SCRIPT_FILE_NAME = "import_instance.gd"


def write_godot_utility(destination, overwrite=True):
    """Write the reusable Godot utility assets into ``destination``.

    Copies the shader and the material-assignment script so they can be dropped
    into a Godot project at ``res://majin_utility/``. Returns the list of
    written paths. The in-repository ``godot/utility/`` folder is the source of
    truth; this function keeps the emitted copy identical to it.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    instance_script_path = (
        Path(__file__).resolve().parents[2]
        / "godot" / "utility" / IMPORT_INSTANCE_SCRIPT_FILE_NAME
    )
    if not instance_script_path.is_file():
        raise FileNotFoundError(
            f"Missing Godot utility source: {instance_script_path}"
        )
    for name, content in (
        (CUSTOM_SHADER_FILE_NAME, GODOT_CUSTOM_SHADER),
        (FOLIAGE_SHADER_FILE_NAME, GODOT_FOLIAGE_SHADER),
        (SHADER_COMMON_FILE_NAME, GODOT_SHADER_COMMON),
        (ASSIGN_SCRIPT_FILE_NAME, GODOT_ASSIGN_SCRIPT),
        (IMPORT_INSTANCE_SCRIPT_FILE_NAME,
         instance_script_path.read_text(encoding="utf-8")),
    ):
        path = destination / name
        if overwrite or not path.exists():
            path.write_text(content, encoding="ascii")
        written.append(path)
    return written


# The repository script is the source of truth. Reading it here prevents the
# installed Godot utility from drifting as character import support evolves.
GODOT_ASSIGN_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "godot" / "utility" / ASSIGN_SCRIPT_FILE_NAME
).read_text(encoding="utf-8")


# Slot 0 is always the material the vertex originally belonged to. Slots 1..3
# are foreign materials reachable within the blend radius.
MAX_BLEND_SLOTS = 4


def _requires_custom_shader(material: dict, uniforms: dict, use_blend=False) -> bool:
    """Whether StandardMaterial3D cannot express this decoded feature set."""
    shader_key = (material.get("render_state") or {}).get("shader_key")
    return bool(
        _is_double_sided_cutout_foliage(material)
        or shader_key == "0x00847725"
        or material.get("detail")
        or use_blend
        or "utility_texture" in uniforms
        or "env_sphere_texture" in uniforms
    )


def _is_double_sided_cutout_foliage(material: dict) -> bool:
    return is_foliage_render_state(material.get("render_state"))


def _safe_resource_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return cleaned or "material"


def _write_shader_material(
    path: Path,
    shader_reference: str,
    uniforms: dict[str, str],
    switches: dict[str, bool],
    scalars: dict[str, float],
    colors: dict[str, list[float]] | None = None,
) -> None:
    resources = [("Shader", shader_reference, "1_shader")]
    texture_ids = {}
    for number, (uniform, texture_path) in enumerate(sorted(uniforms.items()), 2):
        resource_id = f"{number}_{uniform}"
        relative_texture = Path("..") / Path(texture_path)
        resources.append(("Texture2D", relative_texture.as_posix(), resource_id))
        texture_ids[uniform] = resource_id

    lines = [
        f'[gd_resource type="ShaderMaterial" load_steps={len(resources) + 1} format=3]',
        "",
    ]
    lines.extend(
        f'[ext_resource type="{kind}" path="{resource_path}" id="{resource_id}"]'
        for kind, resource_path, resource_id in resources
    )
    lines.extend(["", "[resource]", 'shader = ExtResource("1_shader")'])
    lines.extend(
        f'shader_parameter/{uniform} = ExtResource("{resource_id}")'
        for uniform, resource_id in texture_ids.items()
    )
    lines.extend(
        f'shader_parameter/{name} = {str(enabled).lower()}'
        for name, enabled in switches.items()
    )
    lines.extend(
        f'shader_parameter/{name} = {value:.9g}'
        for name, value in scalars.items()
    )
    lines.extend(
        f'shader_parameter/{name} = Color({value[0]:.9g}, {value[1]:.9g}, '
        f'{value[2]:.9g}, {value[3]:.9g})'
        for name, value in (colors or {}).items()
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="ascii")


def _write_standard_material(
    path: Path,
    uniforms: dict[str, str],
    render_mode: str,
    roughness: float,
    specular: float,
    diffuse: list[float],
    use_vertex_color: bool,
) -> None:
    """Write a native Godot 4 PBR material for a simple DDM pipeline."""
    texture_properties = {
        "base_texture": "albedo_texture",
        "normal_texture": "normal_texture",
    }
    resources = []
    texture_ids = {}
    for number, (uniform, property_name) in enumerate(texture_properties.items(), 1):
        texture_path = uniforms.get(uniform)
        if not texture_path:
            continue
        resource_id = f"{number}_{property_name}"
        relative_texture = (Path("..") / Path(texture_path)).as_posix()
        resources.append((relative_texture, resource_id))
        texture_ids[property_name] = resource_id

    lines = [
        f'[gd_resource type="StandardMaterial3D" load_steps={len(resources) + 1} format=3]',
        "",
    ]
    lines.extend(
        f'[ext_resource type="Texture2D" path="{resource_path}" id="{resource_id}"]'
        for resource_path, resource_id in resources
    )
    lines.extend(["", "[resource]"])
    lines.extend(
        f'{property_name} = ExtResource("{resource_id}")'
        for property_name, resource_id in texture_ids.items()
    )
    if "normal_texture" in texture_ids:
        lines.append("normal_enabled = true")
    transparency = {
        "opaque": 0,
        "alpha_blend": 1,
        "alpha_scissor": 2,
    }.get(render_mode, 0)
    if transparency:
        lines.append(f"transparency = {transparency}")
    if render_mode == "alpha_scissor":
        lines.extend(("alpha_scissor_threshold = 0.5", "cull_mode = 2"))
    lines.extend((
        f"albedo_color = Color({diffuse[0]:.9g}, {diffuse[1]:.9g}, {diffuse[2]:.9g}, 1)",
        f"vertex_color_use_as_albedo = {str(use_vertex_color).lower()}",
        "metallic = 0.0",
        f"metallic_specular = {specular:.9g}",
        f"roughness = {roughness:.9g}",
        "",
    ))
    path.write_text("\n".join(lines), encoding="ascii")


def _write_foliage_material(
    path: Path,
    shader_reference: str,
    base_texture: str,
    diffuse: list[float],
) -> None:
    """Write the albedo-only, two-sided cutout foliage material."""
    relative_texture = (Path("..") / Path(base_texture)).as_posix()
    lines = [
        '[gd_resource type="ShaderMaterial" load_steps=3 format=3]',
        "",
        f'[ext_resource type="Shader" path="{shader_reference}" id="1_shader"]',
        f'[ext_resource type="Texture2D" path="{relative_texture}" id="2_albedo"]',
        "",
        "[resource]",
        'shader = ExtResource("1_shader")',
        'shader_parameter/base_texture = ExtResource("2_albedo")',
        (
            "shader_parameter/albedo_modulate = "
            f"Color({diffuse[0]:.9g}, {diffuse[1]:.9g}, {diffuse[2]:.9g}, 1)"
        ),
        "",
    ]
    path.write_text("\n".join(lines), encoding="ascii")


def _material_texture_map(material: dict) -> dict:
    """Return ``{uniform_name: output_path}`` for a material's own textures.

    A material may carry an additional ``detail`` mapping (``{"diffuse": path,
    "normal": path, "mask": path}``) produced when a coincident multipass
    overlay is folded into the base material. Those become the detail-layer
    uniforms so the shader can combine both texture sets in a single draw.
    """
    uniforms = {}
    for texture in material.get("textures", []):
        uniform = ROLE_TO_UNIFORM.get(texture.get("role"))
        output = texture.get("output")
        if uniform and output:
            uniforms[uniform] = output
    detail = material.get("detail") or {}
    for key, uniform in (
        ("diffuse", "detail_texture"),
        ("normal", "detail_normal_texture"),
        ("mask", "detail_mask"),
    ):
        output = detail.get(key)
        if output:
            uniforms[uniform] = output
    return uniforms


def _multitexture_map(material: dict) -> dict:
    """Bind both albedo/normal pairs of the decoded 0x00847725 variant."""
    textures = sorted(material.get("textures", []), key=lambda item: item.get("slot", 0))
    albedos = [
        texture["output"] for texture in textures
        if texture.get("output")
        and texture.get("role") in {"diffuse", "auxiliary"}
        and texture.get("role") != "normal"
    ]
    normals = [
        texture["output"] for texture in textures
        if texture.get("output") and texture.get("role") == "normal"
    ]
    result = {}
    if albedos:
        result["base_texture"] = albedos[0]
    if len(albedos) > 1:
        result["secondary_texture"] = albedos[1]
    if normals:
        result["normal_texture"] = normals[0]
    if len(normals) > 1:
        result["secondary_normal_texture"] = normals[1]
    return result


def _build_blend_assets(
    out_dir: Path,
    material_dir: Path,
    materials: list[dict],
    mesh_parts: list[dict],
    vertices: list[dict],
    scale: float,
    radius: float,
) -> dict[int, dict]:
    """Generate one blend map per material that has foreign neighbours.

    Returns ``{material_index: {"path": relative_path, "slots": slot_materials}}``
    for materials eligible for blending. Materials with no foreign neighbour in
    range simply get no entry and keep the single-texture fast path.
    """
    material_by_index = {int(m["index"]): m for m in materials}
    # Group mesh parts by their material so each owner only sees its own faces.
    parts_by_material = defaultdict(list)
    for part in mesh_parts:
        parts_by_material[int(part["material_index"])].append(part)

    assets: dict[int, dict] = {}
    for owner_index, owner_parts in parts_by_material.items():
        if owner_index not in material_by_index:
            continue
        # ``select_material_slots`` and the neighbour grid must see *all* mesh
        # parts, otherwise no foreign material is ever in range. Slot 0 is
        # reserved for the owner; the remaining slots go to foreign materials.
        slots = select_material_slots(
            mesh_parts,
            max_slots=MAX_BLEND_MATERIALS,
            owner=owner_index,
        )
        if len(slots) < 2:
            # Only the owner is present near this submesh: nothing to blend.
            continue
        rows, slot_materials = build_blend_map(
            vertices,
            mesh_parts,
            slots,
            scale,
            radius=radius,
            owner_filter=owner_index,
        )
        if not _has_foreign_weight(rows):
            # All weights collapsed to the owner channel: skip the texture.
            continue
        filename = f"blend_{owner_index:02d}.png"
        destination = material_dir / filename
        write_png(destination, BLEND_MAP_SIZE, BLEND_MAP_SIZE, rows)
        assets[owner_index] = {
            "path": destination.relative_to(out_dir).as_posix(),
            "slot_materials": slot_materials,
        }
    return assets


def _has_foreign_weight(rows) -> bool:
    """Whether any texel carries weight outside its dominant (owner) channel."""
    for row in rows:
        for offset in range(0, len(row), 4):
            pixels = row[offset:offset + 4]
            if pixels[1] or pixels[2] or pixels[3]:
                return True
    return False


def _position_signature(vertices, triangle):
    """Orientation-independent position-only signature of a face.

    Multipass overlays paint a *different texture* onto the *same* geometry.
    Candidate detection keys on position; folding separately verifies that UVs
    match before transferring the source alpha mask.
    """
    points = sorted(tuple(vertices[index]['position']) for index in triangle)
    return tuple(points)


def _fold_multipass_details(materials, mesh_parts, vertices):
    """Fold exact multipass overlays into their host material as a detail layer.

    The map renderer can draw a surface once as a base material and again as a
    detail overlay on the *same* geometry with a different texture (for example
    the observed ``gake102__multi`` over ``gake102__base``). Core glTF cannot
    express that, but a Godot shader can combine both texture sets in a single
    draw.

    A material B is treated as an overlay of A when *every* B face shares its
    positions with an A face (100% coverage). The overlay's diffuse and normal
    texture outputs are attached to A as ``material["detail"]``. The overlay
    alpha is copied to the coincident host vertices and the overlay material
    index is reported so its coplanar GLB primitive can be omitted.

    Folding requires matching UVs as well as positions. A layout with distinct
    UV sets needs a second exported UV stream and is deliberately left alone.
    """
    material_by_index = {int(m['index']): m for m in materials}
    roles = {
        int(m['index']): {t.get('role') for t in m.get('textures', [])}
        for m in materials
    }

    faces_by_material = defaultdict(list)
    for part in mesh_parts:
        for triangle in part['triangles']:
            faces_by_material[int(part['material_index'])].append(triangle)

    # ~{position_signature: set(material_index)} sharing the same geometry.
    owners = defaultdict(set)
    for material_index, triangles in faces_by_material.items():
        for triangle in triangles:
            owners[_position_signature(vertices, triangle)].add(material_index)

    def overlay_of(material_index, host_index):
        """Fraction of material_index faces whose geometry also hosts host_index."""
        triangles = faces_by_material[material_index]
        if not triangles:
            return 0.0
        covered = sum(
            1 for triangle in triangles
            if host_index in owners[_position_signature(vertices, triangle)]
        )
        return covered / len(triangles)

    def detail_texture(material, role):
        for texture in material.get('textures', []):
            if texture.get('role') == role and texture.get('output'):
                return texture['output']
        return None

    folds = []
    for index, material in material_by_index.items():
        if 'diffuse' not in roles.get(index, set()) or material.get('detail'):
            continue
        # Find the host the largest fraction of this material sits on.
        best_host, best_ratio = None, 0.0
        for host_index in material_by_index:
            if host_index == index:
                continue
            ratio = overlay_of(index, host_index)
            if ratio > best_ratio:
                best_host, best_ratio = host_index, ratio
        # Only fold when the material is entirely an overlay (no unique faces)
        # and the host is the larger surface. Requiring a strictly larger host
        # keeps the fold direction unambiguous when two materials cover each
        # other exactly (symmetric fixtures) instead of folding both ways.
        if best_host is None or best_ratio < 1.0:
            continue
        if len(faces_by_material[best_host]) <= len(faces_by_material[index]):
            continue
        host = material_by_index[best_host]
        # Never overwrite an existing detail layer.
        if host.get('detail'):
            continue
        # Match every overlay face to a host face and verify that the UV at
        # each shared position is identical. This is true for map101's
        # gake102 pair, so one draw can reproduce both passes losslessly.
        host_faces = {
            _position_signature(vertices, triangle): triangle
            for triangle in faces_by_material[best_host]
        }
        uv_compatible = True
        overlay_alpha = defaultdict(list)
        for triangle in faces_by_material[index]:
            host_triangle = host_faces.get(_position_signature(vertices, triangle))
            if host_triangle is None:
                uv_compatible = False
                break
            host_uv = {
                tuple(vertices[vertex_index]['position']): tuple(vertices[vertex_index]['uv'])
                for vertex_index in host_triangle
            }
            for vertex_index in triangle:
                vertex = vertices[vertex_index]
                position = tuple(vertex['position'])
                if host_uv.get(position) != tuple(vertex['uv']):
                    uv_compatible = False
                    break
                overlay_alpha[position].append(
                    int(vertex.get('color', 0xFFFFFFFF)) & 0xFF
                )
            if not uv_compatible:
                break
        if not uv_compatible:
            continue

        detail = {
            'source_material': material.get('name', f"material_{index}"),
            'source_material_index': index,
            'mask_source': 'vertex_alpha',
            'strength': 1.0,
        }
        diffuse = detail_texture(material, 'diffuse')
        normal = detail_texture(material, 'normal')
        if diffuse:
            detail['diffuse'] = diffuse
        if normal:
            detail['normal'] = normal
        if not detail.get('diffuse'):
            continue

        # COLOR.a on the host becomes the detail mask. Vertices not covered by
        # the overlay must be zero, otherwise the detail would spill outside
        # the source-authored painted region.
        host_vertex_indices = {
            vertex_index
            for triangle in faces_by_material[best_host]
            for vertex_index in triangle
        }
        for vertex_index in host_vertex_indices:
            vertex = vertices[vertex_index]
            values = overlay_alpha.get(tuple(vertex['position']))
            alpha = round(sum(values) / len(values)) if values else 0
            color = int(vertex.get('color', 0xFFFFFFFF))
            vertex['color'] = (color & 0xFFFFFF00) | alpha

        host['detail'] = detail
        folds.append({
            'host': host.get('name', f"material_{best_host}"),
            'detail': detail['source_material'],
            'coverage': round(best_ratio, 4),
            'overlay_material_index': index,
            'mask_source': 'vertex_alpha',
        })
    return folds


def _material_uses_vertex_alpha(material_index, vertices, mesh_parts):
    """Whether a material contains a source vertex alpha below full opacity."""
    if not vertices or not mesh_parts:
        return False
    return any(
        (int(vertices[vertex_index].get('color', 0xFFFFFFFF)) & 0xFF) < 255
        for part in mesh_parts
        if int(part['material_index']) == material_index
        for triangle in part['triangles']
        for vertex_index in triangle
    )


def _mesh_has_source_vertex_alpha(vertices, mesh_parts):
    """Whether the DDM already supplies any non-opaque vertex paint masks."""
    return any(
        (int(vertex.get('color', 0xFFFFFFFF)) & 0xFF) < 255
        for vertex in (vertices or [])
    )


def write_godot_material_assets(
    out_dir: Path,
    materials: list[dict],
    image_data: dict[str, bytes],
    vertices: list[dict] | None = None,
    mesh_parts: list[dict] | None = None,
    scale: float = 1.0,
    radius: float = DEFAULT_BLEND_RADIUS,
    detail_folds: list[dict] | None = None,
    asset_kind: str = "model",
    ik_target_orientation: str = "none",
) -> dict:
    """Write the Godot shader, external PNGs, and their material bindings.

    When ``vertices`` and ``mesh_parts`` are provided and a submesh touches a
    different material, a UV-space blend map is baked for that owner material
    and the shader is switched to the multi-texture mixing path. The
    ``blend_base_texture_N`` uniforms are bound to the *neighbouring* materials
    actually present in range, not to the owner's own textures.
    """
    material_dir = out_dir / "materials"
    material_dir.mkdir(parents=True, exist_ok=True)
    # The shader and assignment script are reusable Godot project assets. They
    # live in ``godot/utility/`` and are installed once under
    # ``res://majin_utility/``; the generated materials reference that stable
    # path instead of regenerating the shader on every export. Use
    # :func:`write_godot_utility` to copy the folder into a Godot project.
    custom_shader_reference = f"{GODOT_UTILITY_DIR}/{CUSTOM_SHADER_FILE_NAME}"
    foliage_shader_reference = f"{GODOT_UTILITY_DIR}/{FOLIAGE_SHADER_FILE_NAME}"

    for relative, payload in image_data.items():
        destination = out_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    # Source-authored alpha masks supersede the old proximity-based blend-map
    # reconstruction. Mixing both systems double-blends terrain and obscures
    # the actual DDM signal. Keep the heuristic only for older layouts that
    # contain no vertex alpha information at all.
    has_source_vertex_alpha = _mesh_has_source_vertex_alpha(vertices, mesh_parts)
    blend_assets: dict[int, dict] = {}
    if vertices and mesh_parts and len(materials) > 1 and not has_source_vertex_alpha:
        blend_assets = _build_blend_assets(
            out_dir, material_dir, materials, mesh_parts, vertices, scale, radius,
        )

    if detail_folds is None and vertices and mesh_parts:
        detail_folds = _fold_multipass_details(materials, mesh_parts, vertices)
    detail_folds = detail_folds or []

    material_by_index = {int(m["index"]): m for m in materials}
    bindings = []
    material_resources = []
    for material in materials:
        uniforms = dict(_material_texture_map(material))
        if (material.get("render_state") or {}).get("shader_key") == "0x00847725":
            uniforms.update(_multitexture_map(material))

        asset = blend_assets.get(int(material["index"]))
        use_blend = bool(asset)
        if use_blend:
            uniforms["blend_map"] = asset["path"]
            for slot, neighbour_index in enumerate(
                asset["slot_materials"][1:], start=1
            ):
                if neighbour_index is None:
                    continue
                neighbour = material_by_index.get(int(neighbour_index))
                if neighbour is None:
                    continue
                neighbour_uniforms = _material_texture_map(neighbour)
                neighbour_diffuse = neighbour_uniforms.get("base_texture")
                if neighbour_diffuse:
                    uniforms[f"blend_base_texture_{slot}"] = neighbour_diffuse

        switches = {
            "use_utility_texture": "utility_texture" in uniforms,
            "use_normal_texture": "normal_texture" in uniforms,
            "use_secondary_texture": "secondary_texture" in uniforms,
            "use_secondary_normal_texture": "secondary_normal_texture" in uniforms,
            "use_env_sphere_texture": "env_sphere_texture" in uniforms,
            "use_blend": use_blend,
            "use_detail": "detail_texture" in uniforms,
            "use_detail_normal_texture": "detail_normal_texture" in uniforms,
            "use_detail_mask": "detail_mask" in uniforms,
            "use_vertex_alpha": (
                not material.get("detail")
                and (material.get("render_state") or {}).get("mode") == "alpha_blend"
                and _material_uses_vertex_alpha(
                    int(material["index"]), vertices, mesh_parts,
                )
            ),
        }
        estimate = material.get("pbr_estimate") or {}
        scalars = {
            "roughness": float(estimate.get("roughness", 1.0)),
            "specular": float(estimate.get("specular", 0.5)),
        }
        diffuse = list(estimate.get("diffuse") or [1.0, 1.0, 1.0])[:3]
        while len(diffuse) < 3:
            diffuse.append(1.0)
        colors = {"albedo_modulate": [*map(float, diffuse), 1.0]}
        if switches["use_detail"]:
            detail = material.get("detail") or {}
            scalars["detail_strength"] = float(detail.get("strength", 0.5))
        resource_name = (
            f'{material["index"]:02d}_'
            f'{_safe_resource_name(material.get("name", "material"))}.tres'
        )
        resource_path = material_dir / resource_name
        custom_shader = _requires_custom_shader(material, uniforms, use_blend)
        foliage_shader = _is_double_sided_cutout_foliage(material)
        render_mode = (material.get("render_state") or {}).get("mode", "opaque")
        if foliage_shader:
            _write_foliage_material(
                resource_path,
                foliage_shader_reference,
                uniforms["base_texture"],
                diffuse,
            )
        elif custom_shader:
            _write_shader_material(
                resource_path, custom_shader_reference, uniforms, switches, scalars,
                colors,
            )
        else:
            _write_standard_material(
                resource_path,
                uniforms,
                render_mode,
                scalars["roughness"],
                scalars["specular"],
                diffuse,
                use_vertex_color=True,
            )
        material_resources.append(resource_path.relative_to(out_dir).as_posix())
        selected_shader = (
            foliage_shader_reference if foliage_shader
            else custom_shader_reference if custom_shader
            else None
        )
        bindings.append({
            "material_index": material["index"],
            "material_name": material.get("name", f'material_{material["index"]}'),
            "material_type": "ShaderMaterial" if custom_shader else "StandardMaterial3D",
            "shader": selected_shader,
            "material_resource": resource_path.relative_to(out_dir).as_posix(),
            "uniforms": uniforms,
            "switches": switches,
            "scalars": scalars,
            "colors": colors,
            "blend_map": asset["path"] if asset else None,
            "detail": material.get("detail"),
            "render_state": material.get("render_state"),
        })

    manifest_path = material_dir / "material_bindings.json"
    manifest_path.write_text(
        json.dumps({
            "asset_kind": asset_kind,
            "ik_target_orientation": ik_target_orientation,
            "materials": bindings,
            "detail_folds": detail_folds,
        }, indent=2)
        + "\n",
        encoding="ascii",
    )
    return {
        "shader": custom_shader_reference,
        "manifest": manifest_path.relative_to(out_dir).as_posix(),
        "shader_materials": material_resources,
        "texture_count": len(image_data),
        "blend_map_count": len(blend_assets),
        "blend_source": (
            "ddm_vertex_alpha" if has_source_vertex_alpha
            else "reconstructed_proximity"
        ),
        "detail_fold_count": len(detail_folds),
    }
