"""Godot 4 material assets for reconstructed DDM shaders."""

from __future__ import annotations

import json
import re
from pathlib import Path


GODOT_SHADER = """shader_type spatial;

// Visual approximation reconstructed from KbBase P31/P33 sampler bindings.
// RSX disassembly shows that EnvSphere RGB is used as signed vector data, not
// simply added as color. Strength and mask polarity remain exposed so this
// portable shader is not mistaken for a byte-exact translation.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform sampler2D utility_texture : hint_default_white;
uniform sampler2D normal_texture : hint_normal;
uniform sampler2D env_sphere_texture : source_color, hint_default_black, repeat_disable;

uniform bool use_utility_texture = false;
uniform bool use_normal_texture = false;
uniform bool use_env_sphere_texture = false;
uniform bool invert_utility = false;
uniform float reflection_strength : hint_range(0.0, 2.0, 0.01) = 1.0;
uniform float roughness : hint_range(0.0, 1.0, 0.01);

void fragment() {
    vec4 base = texture(base_texture, UV);
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


def write_godot_material_assets(
    out_dir: Path,
    materials: list[dict],
    image_data: dict[str, bytes],
) -> dict:
    """Write the Godot shader, external PNGs, and their material bindings."""
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

    bindings = []
    material_resources = []
    for material in materials:
        uniforms = {}
        for texture in material.get("textures", []):
            uniform = ROLE_TO_UNIFORM.get(texture.get("role"))
            output = texture.get("output")
            if uniform and output:
                uniforms[uniform] = output
        switches = {
            "use_utility_texture": "utility_texture" in uniforms,
            "use_normal_texture": "normal_texture" in uniforms,
            "use_env_sphere_texture": "env_sphere_texture" in uniforms,
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
    }
