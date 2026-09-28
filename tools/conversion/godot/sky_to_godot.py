#!/usr/bin/env python3
"""Reconstruct a Godot 4 day/night sky from Majin sky resources.

The original game does not store a daytime panorama.  HexaEngine combines two
noise textures with sun/cloud/fog colors in ``KbProcedualCloud`` and overlays a
textured star dome at night.  This converter preserves those source textures
and builds a portable Godot approximation of that pipeline.

The input may be the original ``static.pak`` or an extracted directory that
contains ``KB/texture/pro_cloud{0,1}`` and ``KB/map/star/sky_star``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.conversion import xet_to_png as xet  # noqa: E402
from tools.extraction import pak_extractor  # noqa: E402


RESOURCE_PATHS = {
    "cloud0": "KB/texture/pro_cloud0",
    "cloud1": "KB/texture/pro_cloud1",
    "stars": "KB/map/star/sky_star",
    "original_shader": "KB/shader/KbProcedualCloud",
}


SKY_SHADER = """shader_type sky;

// Portable reconstruction of HexaEngine's KbProcedualCloud + star dome.
// The original RSX shader exposes pcloudSunColor, pcloudColor,
// pcloudShadowColor, fogColor, dirLightVec and two procedural textures.
uniform sampler2D cloud_noise_0 : repeat_enable, filter_linear_mipmap;
uniform sampler2D cloud_noise_1 : repeat_enable, filter_linear_mipmap;
uniform sampler2D star_texture : source_color, repeat_enable, filter_nearest;

uniform float time_of_day : hint_range(0.0, 1.0) = 0.25;
uniform float cloud_coverage : hint_range(0.0, 1.0) = 0.42;
uniform float cloud_density : hint_range(0.0, 2.0) = 1.0;
uniform float cloud_speed : hint_range(0.0, 0.1, 0.0001) = 0.004;
uniform float star_intensity : hint_range(0.0, 4.0) = 1.35;
uniform float star_scale : hint_range(1.0, 8.0, 0.25) = 4.0;
uniform float horizon_haze : hint_range(0.0, 1.0) = 0.32;

uniform vec4 zenith_day : source_color = vec4(0.055, 0.24, 0.62, 1.0);
uniform vec4 horizon_day : source_color = vec4(0.48, 0.72, 0.94, 1.0);
uniform vec4 zenith_night : source_color = vec4(0.002, 0.006, 0.025, 1.0);
uniform vec4 horizon_night : source_color = vec4(0.025, 0.045, 0.09, 1.0);
uniform vec4 sunset_color : source_color = vec4(1.0, 0.29, 0.075, 1.0);
uniform vec4 cloud_day : source_color = vec4(0.94, 0.96, 1.0, 1.0);
uniform vec4 cloud_shadow : source_color = vec4(0.22, 0.28, 0.38, 1.0);
uniform vec4 cloud_night : source_color = vec4(0.035, 0.055, 0.105, 1.0);

vec3 sun_direction(float phase) {
    float angle = phase * TAU;
    return normalize(vec3(cos(angle), sin(angle), 0.22));
}

vec2 spherical_uv(vec3 direction) {
    // The source is the square texture used by the game's hemisphere dome.
    // An azimuth/elevation mapping retains its sparse distribution in a Sky.
    return vec2(
        atan(direction.x, direction.z) / TAU + 0.5,
        asin(clamp(direction.y, -1.0, 1.0)) / PI + 0.5
    );
}

void sky() {
    vec3 view = normalize(EYEDIR);
    vec3 sun = sun_direction(time_of_day);
    float daylight = smoothstep(-0.12, 0.10, sun.y);
    float twilight = (1.0 - smoothstep(0.0, 0.30, abs(sun.y))) *
        smoothstep(-0.25, 0.02, sun.y);

    float elevation = clamp(view.y * 0.5 + 0.5, 0.0, 1.0);
    float vertical = pow(elevation, 0.62);
    vec3 day_gradient = mix(horizon_day.rgb, zenith_day.rgb, vertical);
    vec3 night_gradient = mix(horizon_night.rgb, zenith_night.rgb, vertical);
    vec3 color = mix(night_gradient, day_gradient, daylight);

    float toward_sun = max(dot(view, sun), 0.0);
    float sunset_glow = pow(toward_sun, 7.0) * twilight;
    color = mix(color, sunset_color.rgb, sunset_glow * 0.78);
    color += vec3(1.0, 0.72, 0.38) * pow(toward_sun, 700.0) * daylight * 5.0;

    float night = 1.0 - smoothstep(-0.14, 0.04, sun.y);
    vec2 stars_uv = spherical_uv(view) * star_scale;
    vec3 stars_sample = texture(star_texture, stars_uv).rgb;
    // Nearest filtering and a luminance threshold keep the source's one-pixel
    // stars from becoming large blurred discs in the generated sky radiance.
    float star_luminance = max(stars_sample.r, max(stars_sample.g, stars_sample.b));
    float star_mask = smoothstep(0.32, 0.82, star_luminance);
    vec3 stars = normalize(stars_sample + vec3(0.0001)) * star_mask;
    color += stars * star_intensity * night * smoothstep(-0.18, 0.18, view.y);

    // Spherical UVs avoid the severe horizon stretching caused by dividing
    // XZ by elevation. The XET channels are packed procedural data rather
    // than literal cloud photographs.
    vec2 cloud_uv = spherical_uv(view) * vec2(2.0, 1.15);
    vec2 drift = vec2(TIME * cloud_speed, TIME * cloud_speed * 0.37);
    vec4 noise0 = texture(cloud_noise_0, cloud_uv + drift);
    vec4 noise1 = texture(cloud_noise_1, cloud_uv * 1.91 - drift.yx * 0.71);
    float coarse = noise0.r * 0.68 + noise0.g * 0.32;
    float detail = noise1.r * 0.55 + noise1.g * 0.30 + noise1.b * 0.15;
    float field = coarse * 0.68 + detail * 0.32;
    float clouds = smoothstep(cloud_coverage, cloud_coverage + 0.20, field);
    clouds *= smoothstep(-0.08, 0.25, view.y) * cloud_density;

    float lit_edge = smoothstep(0.28, 0.95, coarse + toward_sun * 0.25);
    vec3 daytime_cloud = mix(cloud_shadow.rgb, cloud_day.rgb, lit_edge);
    vec3 cloud_color = mix(cloud_night.rgb, daytime_cloud, daylight);
    cloud_color = mix(cloud_color, sunset_color.rgb, twilight * toward_sun * 0.55);
    color = mix(color, cloud_color, clamp(clouds, 0.0, 0.93));

    float haze = pow(1.0 - abs(clamp(view.y, -1.0, 1.0)), 9.0);
    vec3 fog = mix(horizon_night.rgb, horizon_day.rgb, daylight);
    COLOR = mix(color, fog, haze * horizon_haze);
}
"""


DAY_NIGHT_SCRIPT = """@tool
extends Node3D

@export_range(0.0, 1.0, 0.001) var time_of_day := 0.25:
    set(value):
        time_of_day = fposmod(value, 1.0)
        _apply_time()
@export var run_cycle := true
@export_range(1.0, 3600.0, 1.0) var cycle_duration_seconds := 240.0

@onready var world_environment: WorldEnvironment = $WorldEnvironment
@onready var sun: DirectionalLight3D = $Sun

func _ready() -> void:
    _apply_time()

func _process(delta: float) -> void:
    if run_cycle and not Engine.is_editor_hint():
        time_of_day = fposmod(time_of_day + delta / cycle_duration_seconds, 1.0)

func _apply_time() -> void:
    if not is_node_ready():
        return
    var environment := world_environment.environment
    if environment and environment.sky and environment.sky.sky_material:
        environment.sky.sky_material.set_shader_parameter("time_of_day", time_of_day)

    var angle := time_of_day * TAU
    var elevation := sin(angle)
    # A Godot DirectionalLight emits along local -Z. At 0.25 (noon), an X
    # rotation of -90 degrees therefore points its rays straight downward.
    sun.rotation_degrees = Vector3(time_of_day * 360.0 - 180.0, -12.4, 0.0)
    sun.light_energy = max(0.0, smoothstep(-0.12, 0.08, elevation))
    var warm := 1.0 - smoothstep(0.0, 0.35, abs(elevation))
    sun.light_color = Color(1.0, lerp(0.58, 0.96, 1.0 - warm), lerp(0.34, 0.90, 1.0 - warm))
"""


def _gd_path(path: Path) -> str:
    return "res://" + path.as_posix().lstrip("/")


def _extract_resources_from_pak(path: Path) -> dict[str, bytes]:
    wanted = set(RESOURCE_PATHS.values())
    resources = {}
    for name, _resource_type, payload in pak_extractor.indexed_resources(path.read_bytes()):
        normalized = name.replace("\\", "/")
        if normalized in wanted:
            resources[normalized] = payload
    return resources


def _find_resources_in_directory(root: Path) -> dict[str, bytes]:
    resources = {}
    for logical_path in RESOURCE_PATHS.values():
        direct = root / logical_path
        candidates = [direct] if direct.is_file() else []
        if not candidates:
            candidates = [
                candidate for candidate in root.rglob(Path(logical_path).name)
                if candidate.is_file()
                and candidate.as_posix().endswith(logical_path)
            ]
        if candidates:
            resources[logical_path] = candidates[0].read_bytes()
    return resources


def load_sky_resources(source: Path) -> dict[str, bytes]:
    if source.is_file():
        if source.read_bytes()[:4] != pak_extractor.PAK_MAGIC:
            raise ValueError(f"{source} is not a Game Republic PAK archive")
        resources = _extract_resources_from_pak(source)
    elif source.is_dir():
        resources = _find_resources_in_directory(source)
    else:
        raise FileNotFoundError(source)

    required = {RESOURCE_PATHS[key] for key in ("cloud0", "cloud1", "stars")}
    missing = sorted(required - resources.keys())
    if missing:
        raise FileNotFoundError(
            "missing sky resources: " + ", ".join(missing)
            + "; use static.pak or its extracted contents"
        )
    return resources


def _write_xet_png(payload: bytes, destination: Path) -> dict:
    if len(payload) < 0x88 or payload[:4] != xet.MAGIC:
        raise ValueError(f"invalid XET resource for {destination.name}")
    width = int.from_bytes(payload[0x80:0x82], "big")
    height = int.from_bytes(payload[0x82:0x84], "big")
    offset, texture_format = xet.find_offset_and_format(payload, width, height)
    if texture_format == "DXT5":
        rgba = xet.decode_dxt5_xet(
            payload[offset:offset + xet.dxt5_size(width, height)],
            width,
            height,
            color_first=xet.dxt5_color_first(payload),
        )
    else:
        rgba = xet.decode_dxt1(
            payload[offset:offset + xet.dxt1_size(width, height)], width, height,
        )
    image = xet.Image.frombytes("RGBA", (width, height), rgba)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return {"width": width, "height": height, "format": texture_format}


def write_godot_sky(source: Path, output: Path, resource_prefix: Path) -> dict:
    resources = load_sky_resources(source)
    output.mkdir(parents=True, exist_ok=True)
    textures = output / "textures"

    texture_outputs = {
        "cloud0": textures / "pro_cloud0.png",
        "cloud1": textures / "pro_cloud1.png",
        "stars": textures / "sky_star.png",
    }
    texture_info = {}
    for key, destination in texture_outputs.items():
        logical_path = RESOURCE_PATHS[key]
        texture_info[key] = {
            "source": logical_path,
            "sha256": hashlib.sha256(resources[logical_path]).hexdigest(),
            **_write_xet_png(resources[logical_path], destination),
        }

    shader_path = output / "majin_day_night_sky.gdshader"
    material_path = output / "majin_day_night_sky_material.tres"
    sky_path = output / "majin_day_night_sky.tres"
    environment_path = output / "majin_day_night_environment.tres"
    controller_path = output / "MajinDayNightSky.gd"
    scene_path = output / "majin_day_night_sky_demo.tscn"

    shader_path.write_text(SKY_SHADER, encoding="utf-8")
    controller_path.write_text(DAY_NIGHT_SCRIPT, encoding="utf-8")

    project_output = resource_prefix / output.name
    shader_res = _gd_path(project_output / shader_path.name)
    cloud0_res = _gd_path(project_output / "textures/pro_cloud0.png")
    cloud1_res = _gd_path(project_output / "textures/pro_cloud1.png")
    stars_res = _gd_path(project_output / "textures/sky_star.png")
    material_res = _gd_path(project_output / material_path.name)
    sky_res = _gd_path(project_output / sky_path.name)
    environment_res = _gd_path(project_output / environment_path.name)
    controller_res = _gd_path(project_output / controller_path.name)

    material_path.write_text(f'''[gd_resource type="ShaderMaterial" load_steps=5 format=3]\n\n[ext_resource type="Shader" path="{shader_res}" id="1_shader"]\n[ext_resource type="Texture2D" path="{cloud0_res}" id="2_cloud0"]\n[ext_resource type="Texture2D" path="{cloud1_res}" id="3_cloud1"]\n[ext_resource type="Texture2D" path="{stars_res}" id="4_stars"]\n\n[resource]\nshader = ExtResource("1_shader")\nshader_parameter/cloud_noise_0 = ExtResource("2_cloud0")\nshader_parameter/cloud_noise_1 = ExtResource("3_cloud1")\nshader_parameter/star_texture = ExtResource("4_stars")\nshader_parameter/time_of_day = 0.25\nshader_parameter/cloud_coverage = 0.42\nshader_parameter/cloud_density = 1.0\nshader_parameter/cloud_speed = 0.004\nshader_parameter/star_intensity = 1.35\nshader_parameter/star_scale = 3.0\nshader_parameter/horizon_haze = 0.32\n''', encoding="utf-8")
    material_path.write_text(
        material_path.read_text(encoding="utf-8").replace(
            "shader_parameter/star_scale = 3.0",
            "shader_parameter/star_scale = 4.0",
        ),
        encoding="utf-8",
    )

    sky_path.write_text(f'''[gd_resource type="Sky" load_steps=2 format=3]\n\n[ext_resource type="Material" path="{material_res}" id="1_material"]\n\n[resource]\nsky_material = ExtResource("1_material")\nprocess_mode = 3\n''', encoding="utf-8")

    environment_path.write_text(f'''[gd_resource type="Environment" load_steps=2 format=3]\n\n[ext_resource type="Sky" path="{sky_res}" id="1_sky"]\n\n[resource]\nbackground_mode = 2\nsky = ExtResource("1_sky")\nambient_light_source = 3\nreflected_light_source = 2\ntonemap_mode = 2\n''', encoding="utf-8")

    scene_path.write_text(f'''[gd_scene load_steps=3 format=3]\n\n[ext_resource type="Script" path="{controller_res}" id="1_script"]\n[ext_resource type="Environment" path="{environment_res}" id="2_environment"]\n\n[node name="MajinDayNightSky" type="Node3D"]\nscript = ExtResource("1_script")\n\n[node name="WorldEnvironment" type="WorldEnvironment" parent="."]\nenvironment = ExtResource("2_environment")\n\n[node name="Sun" type="DirectionalLight3D" parent="."]\nrotation_degrees = Vector3(-90, -12.4, 0)\nshadow_enabled = true\ndirectional_shadow_max_distance = 200.0\n''', encoding="utf-8")

    original_shader = resources.get(RESOURCE_PATHS["original_shader"])
    manifest = {
        "generator": "tools/conversion/godot/sky_to_godot.py",
        "source": str(source),
        "reconstruction": "HexaEngine KbProcedualCloud approximation for Godot 4",
        "textures": texture_info,
        "original_shader": {
            "source": RESOURCE_PATHS["original_shader"],
            "found": original_shader is not None,
            "sha256": hashlib.sha256(original_shader).hexdigest()
            if original_shader is not None else None,
            "observed_parameters": [
                "pcloudParam0", "pcloudPos", "pcloudParam1", "pcloudParam2",
                "pcloudSunColor", "pcloudColor", "pcloudShadowColor",
                "fogColor", "dirLightVec",
            ],
        },
        "godot_scene": scene_path.name,
    }
    (output / "sky_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source", type=Path,
        help="static.pak or a directory containing its extracted KB resources",
    )
    parser.add_argument("output", type=Path, help="Godot asset output directory")
    parser.add_argument(
        "--resource-prefix", type=Path, default=Path("."),
        help=(
            "directory below res:// that will contain OUTPUT's directory "
            "(default: project root)"
        ),
    )
    args = parser.parse_args()

    manifest = write_godot_sky(args.source, args.output, args.resource_prefix)
    print(f"[OK] Godot sky: {args.output / manifest['godot_scene']}")
    print("     Source textures: pro_cloud0, pro_cloud1, sky_star")
    print("     Open the generated .tscn or assign the Environment .tres.")


if __name__ == "__main__":
    main()
