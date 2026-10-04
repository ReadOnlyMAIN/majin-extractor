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
except ImportError:
    from blend_mask import (
        BLEND_MAP_SIZE,
        DEFAULT_BLEND_RADIUS,
        MAX_BLEND_MATERIALS,
        build_blend_map,
        select_material_slots,
        write_png,
    )

GODOT_SHADER = """shader_type spatial;

// Visual approximation reconstructed from KbBase P31/P33 sampler bindings.
// RSX disassembly shows that EnvSphere RGB is used as signed vector data, not
// simply added as color. Strength and mask polarity remain exposed so this
// portable shader is not mistaken for a byte-exact translation.
//
// Multi-material blending: the original game transitions smoothly between
// submesh materials. The exporter writes a UV-space blend map whose RGBA
// channels hold the weights of up to four material slots. When use_blend is
// enabled, this shader interpolates between the corresponding base-color
// textures at the seams. Vertices fully inside one submesh keep a single
// dominant channel, so the single-material fast path is unchanged.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform sampler2D utility_texture : hint_default_white;
uniform sampler2D normal_texture : hint_normal;
uniform sampler2D env_sphere_texture : source_color, hint_default_black, repeat_disable;

uniform sampler2D blend_map : hint_default_white, repeat_disable;
uniform sampler2D blend_base_texture_1 : source_color, hint_default_white;
uniform sampler2D blend_base_texture_2 : source_color, hint_default_white;
uniform sampler2D blend_base_texture_3 : source_color, hint_default_white;

uniform bool use_utility_texture = false;
uniform bool use_normal_texture = false;
uniform bool use_env_sphere_texture = false;
uniform bool invert_utility = false;
uniform bool use_blend = false;
uniform float reflection_strength : hint_range(0.0, 2.0, 0.01) = 1.0;
uniform float roughness : hint_range(0.0, 1.0, 0.01);

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
    vec3 view_normal = normalize(NORMAL);

    if (use_normal_texture) {
        vec3 sampled_normal = texture(normal_texture, UV).rgb;
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

    ALBEDO = reconstructed;
    ALPHA = base.a;
    ALPHA_SCISSOR_THRESHOLD = 0.5;
    METALLIC = 0.0;
    ROUGHNESS = roughness;
}
"""


ROLE_TO_UNIFORM = {
    "diffuse": "base_texture",
    "normal": "normal_texture",
    "utility_mask": "utility_texture",
    "matcap": "env_sphere_texture",
}

# Slot 0 is always the material the vertex originally belonged to. Slots 1..3
# are foreign materials reachable within the blend radius.
MAX_BLEND_SLOTS = 4


def _safe_resource_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return cleaned or "material"


def _write_shader_material(
    path: Path,
    shader_reference: str,
    uniforms: dict[str, str],
    switches: dict[str, bool],
    scalars: dict[str, float],
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
    lines.append("")
    path.write_text("\n".join(lines), encoding="ascii")


def _material_texture_map(material: dict) -> dict:
    """Return ``{uniform_name: output_path}`` for a material's own textures."""
    uniforms = {}
    for texture in material.get("textures", []):
        uniform = ROLE_TO_UNIFORM.get(texture.get("role"))
        output = texture.get("output")
        if uniform and output:
            uniforms[uniform] = output
    return uniforms


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


def write_godot_material_assets(
    out_dir: Path,
    materials: list[dict],
    image_data: dict[str, bytes],
    vertices: list[dict] | None = None,
    mesh_parts: list[dict] | None = None,
    scale: float = 1.0,
    radius: float = DEFAULT_BLEND_RADIUS,
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
    # All sibling model directories use the exact same shader. Keep one copy
    # beside those directories instead of duplicating it inside every model.
    shader_path = out_dir.parent / "majin_original.gdshader"
    shader_path.write_text(GODOT_SHADER, encoding="ascii")
    shader_reference = Path("..", "..", shader_path.name).as_posix()

    for relative, payload in image_data.items():
        destination = out_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    blend_assets: dict[int, dict] = {}
    if vertices and mesh_parts and len(materials) > 1:
        blend_assets = _build_blend_assets(
            out_dir, material_dir, materials, mesh_parts, vertices, scale, radius,
        )

    material_by_index = {int(m["index"]): m for m in materials}
    bindings = []
    material_resources = []
    for material in materials:
        uniforms = dict(_material_texture_map(material))

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
            "use_env_sphere_texture": "env_sphere_texture" in uniforms,
            "use_blend": use_blend,
        }
        estimate = material.get("pbr_estimate") or {}
        scalars = {"roughness": float(estimate.get("roughness", 1.0))}
        resource_name = (
            f'{material["index"]:02d}_'
            f'{_safe_resource_name(material.get("name", "material"))}.tres'
        )
        resource_path = material_dir / resource_name
        _write_shader_material(
            resource_path, shader_reference, uniforms, switches, scalars,
        )
        material_resources.append(resource_path.relative_to(out_dir).as_posix())
        bindings.append({
            "material_index": material["index"],
            "material_name": material.get("name", f'material_{material["index"]}'),
            "shader": (Path("..") / shader_path.name).as_posix(),
            "shader_material": resource_path.relative_to(out_dir).as_posix(),
            "uniforms": uniforms,
            "switches": switches,
            "scalars": scalars,
            "blend_map": asset["path"] if asset else None,
        })

    manifest_path = material_dir / "material_bindings.json"
    manifest_path.write_text(
        json.dumps({"materials": bindings}, indent=2) + "\n",
        encoding="ascii",
    )
    return {
        "shader": (Path("..") / shader_path.name).as_posix(),
        "manifest": manifest_path.relative_to(out_dir).as_posix(),
        "shader_materials": material_resources,
        "texture_count": len(image_data),
        "blend_map_count": len(blend_assets),
    }

