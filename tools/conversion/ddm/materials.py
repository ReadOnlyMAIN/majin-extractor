"""DDM material parsing, texture resolution and PBR estimates."""
from __future__ import annotations

import io
import math
import re
import struct
from pathlib import Path

from .binary import be_u32


DEFAULT_EXPORT_SCALE = 0.01


SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\\/\-]+$")


_TEXTURE_INDEX_CACHE = {}


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
        render_state = decode_material_render_state(data, phong)
        pbr_estimate = phong_to_pbr_estimate(phong)
        apply_render_state_pbr_policy(pbr_estimate, render_state)
        materials.append({
            "index": material_index,
            "name": strings[string_index]["text"],
            "name_offset": strings[string_index]["offset"],
            "texture_references": [
                item["text"]
                for item in material_strings[1:]
            ],
            "phong": phong,
            "render_state": render_state,
            "pbr_estimate": pbr_estimate,
        })
    return materials


MATERIAL_BLEND_MODES = {
    0: "opaque",
    1: "alpha_scissor",
    2: "alpha_blend",
}


FOLIAGE_ALPHA_SCISSOR_SHADER_KEY = "0x00843105"
FOLIAGE_ROUGHNESS = 1.0
MAP_SURFACE_SHADER_KEYS = {
    "0x00807125",  # feathered terrain/detail
    "0x00847125",  # ordinary opaque map surface / folded pass
    "0x00847725",  # two-albedo/two-normal terrain blend
}
MAP_SURFACE_SPECULAR = 0.2


def apply_render_state_pbr_policy(estimate, render_state):
    """Apply shader-family semantics that are not present in the Phong block.

    Map101's double-sided cutout foliage has a dedicated shader key but reuses
    the generic Ks=.8/Ns=32 authoring template found on stone and architecture.
    Treating that exponent as an ordinary surface produces implausibly tight,
    wet highlights. Keep the mathematical conversion for provenance and make
    the effective Godot/glTF foliage material fully rough and non-specular.
    """
    if not estimate or not render_state:
        return estimate
    if (
        render_state.get("mode") == "alpha_scissor"
        and render_state.get("shader_key") == FOLIAGE_ALPHA_SCISSOR_SHADER_KEY
    ):
        estimate["roughness_from_phong"] = estimate.get("roughness")
        estimate["specular_from_phong"] = estimate.get("specular")
        estimate["roughness"] = FOLIAGE_ROUGHNESS
        estimate["specular"] = 0.0
        estimate["roughness_source"] = (
            "double_sided_cutout_foliage_shader_family"
        )
        estimate["specular_source"] = (
            "double_sided_cutout_foliage_albedo_only"
        )
    elif (
        render_state.get("shader_key") in MAP_SURFACE_SHADER_KEYS
        and any(value > 0.0 for value in estimate.get("specular_rgb", ()))
    ):
        # The DDM Ks values distinguish authoring records, but no evidence
        # shows that they map linearly to Godot's dielectric SPECULAR control.
        # A shared low response avoids a visible reflectance discontinuity
        # between the two-texture zimen terrain and its neighbouring surfaces,
        # while retaining sky/reflection-probe influence.
        # Preserve an explicit Ks=0: Map102/Map103 prove that the same terrain
        # shader family also contains materials whose legacy lobe is disabled.
        estimate["specular_from_phong"] = estimate.get("specular")
        estimate["specular"] = MAP_SURFACE_SPECULAR
        estimate["specular_source"] = (
            "shared_map_surface_visual_calibration"
        )
    return estimate


def decode_material_render_state(data: bytes, phong):
    """Decode the render-mode byte and packed shader key before Phong data.

    Map101 places a nine-byte material-variant trailer immediately before
    the eleven-float Phong block::

        uint8  blend_mode;       // 0 opaque, 1 alpha test, 2 alpha blend/pass
        uint32 shader_key;       // feature bits still under study
        uint32 variant_word;     // observed 0 or 2; exact meaning unresolved

    The mode assignments are supported independently by geometry and texture
    evidence: mode 1 is used by cutout foliage with texture alpha, while mode
    2 is used by terrain-paint passes with vertex alpha. Return ``None`` for a
    different layout instead of guessing from names.
    """
    if not phong:
        return None
    offset = int(phong["offset"])
    if offset < 9:
        return None
    variant_word = be_u32(data, offset - 4)
    # Map101 consistently uses 2, while several otherwise identical Map102
    # and Map103 records use 0.  Both forms retain the same mode/key layout.
    # Keep the value explicit because its component-level meaning is not yet
    # established; accepting arbitrary words here would weaken the structural
    # check and make false-positive Phong matches more likely.
    if variant_word not in (0, 2):
        return None
    mode_value = data[offset - 9]
    mode = MATERIAL_BLEND_MODES.get(mode_value)
    if mode is None:
        return None
    shader_key = be_u32(data, offset - 8)
    # All correlated KbBase map feature keys occupy the 0x008xxxxx range.
    # This also keeps a zero-filled control block from passing merely because
    # zero is valid for both the opaque mode and the newly observed word.
    if (shader_key & 0xFFF00000) != 0x00800000:
        return None
    return {
        "mode_value": mode_value,
        "mode": mode,
        "shader_key": f"0x{shader_key:08x}",
        "variant_word": variant_word,
        "offset": offset - 9,
        "confidence": "high",
    }


def find_phong_parameters(data: bytes, start: int, end: int):
    """Find the observed diffuse-RGBA/unknown/Ks/Ns material sequence.

    The serialized record contains diffuse RGBA, two incompletely understood
    scalars, specular RGB, another scalar, and a Phong/Blinn-Phong shininess
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
        diffuse = values[:3]
        diffuse_alpha = values[3]
        unknown_after_diffuse = values[4:6]
        specular = values[6:9]
        trailing_scalar = values[9]
        shininess = values[10]

        if not all(math.isfinite(value) for value in values):
            continue
        if not all(-2.001 <= value <= 4.0 for value in diffuse + specular):
            continue
        if sum(abs(value) for value in diffuse + specular) < 0.01:
            continue
        if not -0.001 <= diffuse_alpha <= 1.001:
            continue
        if not all(-2.001 <= value <= 4.0 for value in unknown_after_diffuse):
            continue
        if not -0.001 <= trailing_scalar <= 1.001:
            continue
        # A zero exponent is valid when Ks is also zero: Map102/kusa5 uses
        # exactly that combination to disable the legacy specular lobe.  A
        # nonzero Ks still needs a plausible exponent so an all-zero padding
        # run cannot masquerade as a material record.
        # Require the serialized IEEE values to be exactly zero. Tiny
        # denormals occur in neighbouring integer/control records and are not
        # evidence of a deliberately disabled material lobe.
        zero_specular = all(value == 0.0 for value in specular)
        if not (
            2.0 <= shininess <= 512.0
            or (zero_specular and shininess == 0.0)
        ):
            continue

        # Real RGB triplets tend to have related components. Only score the
        # confirmed diffuse and specular fields: treating diffuse alpha and
        # the two intervening scalars as an ambient triplet made map records
        # easier to match at the wrong byte alignment.
        score = sum(
            abs(color[index] - color[index + 1])
            for color in (diffuse, specular)
            for index in (0, 1)
        )
        score += 4.0 * sum(
            max(0.0, value - 1.01) for value in diffuse
        )
        score -= min(shininess, 128.0) / 10000.0
        candidates.append((score, offset, values))

    if not candidates:
        return None

    _, offset, values = min(candidates, key=lambda candidate: candidate[0])
    return {
        "offset": offset,
        "diffuse": list(values[0:3]),
        "diffuse_alpha": values[3],
        "unknown_after_diffuse": list(values[4:6]),
        "specular": list(values[6:9]),
        "unknown_scalar_after_specular": values[9],
        "shininess": values[10],
        "encoding": "legacy_phong_candidate",
    }


def phong_to_pbr_estimate(phong):
    """Return explicitly derived PBR metadata without inventing source data."""
    if not phong:
        return None

    shininess = max(0.0, float(phong["shininess"]))
    # Godot and glTF use a Schlick-GGX lobe, not Beckmann. Match the half-power
    # angle of the source Blinn-Phong cos(Ns) lobe to the GGX NDF. If
    # c2=cos(theta_half)^2=2^(-2/Ns), solving D_GGX(theta)/D_GGX(0)=1/2 gives
    # alpha^2=(1-c2)/(sqrt(2)-c2). Perceptual roughness is sqrt(alpha).
    if shininess <= 0.0:
        roughness = 1.0
        roughness_source = "disabled_legacy_specular_lobe"
    else:
        half_power_cosine_squared = 2.0 ** (-2.0 / shininess)
        alpha_squared = (
            (1.0 - half_power_cosine_squared)
            / (math.sqrt(2.0) - half_power_cosine_squared)
        )
        roughness = min(1.0, max(0.0, alpha_squared ** 0.25))
        roughness_source = "blinn_phong_to_ggx_half_power_match"

    specular_rgb = [
        min(1.0, max(0.0, float(value)))
        for value in phong.get("specular", (1.0, 1.0, 1.0))[:3]
    ]
    while len(specular_rgb) < 3:
        specular_rgb.append(specular_rgb[-1] if specular_rgb else 1.0)
    specular_luminance = (
        0.2126 * specular_rgb[0]
        + 0.7152 * specular_rgb[1]
        + 0.0722 * specular_rgb[2]
    )
    # KHR_materials_specular multiplies the conventional dielectric F0=0.04 by
    # Ks. Godot's scalar value 0.5 represents that same baseline, so 0.5*Ks
    # preserves the relative source strength for Map101's neutral-grey Ks.
    godot_specular = min(1.0, max(0.0, 0.5 * specular_luminance))
    return {
        "metallic": None,
        "roughness": roughness,
        "roughness_source": roughness_source,
        "roughness_formula": (
            "pow((1 - pow(2, -2 / Ns)) / "
            "(sqrt(2) - pow(2, -2 / Ns)), 0.25)"
        ),
        "specular": godot_specular,
        "specular_source": "0.5_times_legacy_Ks_luminance",
        "specular_rgb": specular_rgb,
        "diffuse": [
            min(1.0, max(0.0, float(value)))
            for value in phong.get("diffuse", (1.0, 1.0, 1.0))[:3]
        ],
        "metallic_source": "no_conductor_parameter_in_ddm",
    }


def stabilize_map_pbr_estimates(materials):
    """Give unsupported map records a stable, explicitly weak fallback.

    Map DDMs commonly repeat one legacy Blinn-Phong template across unrelated
    surfaces. If a material variant lacks that block entirely, use the median
    roughness from the same DDM rather than glTF's unrelated default of 1.0.
    """
    values = sorted(
        estimate["roughness"]
        for material in materials
        if (estimate := material.get("pbr_estimate")) is not None
        and estimate.get("roughness") is not None
    )
    if not values:
        return materials
    middle = len(values) // 2
    median = (
        values[middle]
        if len(values) % 2
        else (values[middle - 1] + values[middle]) / 2.0
    )
    for material in materials:
        estimate = material.get("pbr_estimate")
        if estimate is None or estimate.get("roughness") is None:
            material["pbr_estimate"] = {
                "metallic": None,
                "roughness": median,
                "roughness_source": "same_map_material_median_fallback",
                "confidence": 0.25,
                "warning": "legacy_material_variant_has_no_decoded_shininess",
            }
        else:
            estimate.setdefault("confidence", 0.5)
            estimate.setdefault(
                "warning",
                "legacy_map_shininess_may_be_an_authoring_default",
            )
    return materials


def texture_storage_names(reference: str):
    names = [reference]
    match = re.fullmatch(r"(.+)_c(?:\d+)?", reference, re.IGNORECASE)
    if match:
        names.append(match.group(1))
    return list(dict.fromkeys(names))


def texture_file_index(root: Path):
    """Index a resource tree once so recursive conversions stay inexpensive."""
    key = str(root.resolve(strict=False)).lower()
    if key not in _TEXTURE_INDEX_CACHE:
        index = {}
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    index.setdefault(path.name.lower(), []).append(path)
        _TEXTURE_INDEX_CACHE[key] = index
    return _TEXTURE_INDEX_CACHE[key]


def texture_candidate_rank(path: Path, model_path: Path):
    """Prefer canonical common textures matching the map's area family."""
    normalized = path.as_posix().lower()
    score = 0
    if "/texture/common/" in normalized:
        score += 100
    match = re.search(r"map(\d)", model_path.as_posix(), re.IGNORECASE)
    if match and f"/area{match.group(1)}/" in normalized:
        score += 50
    return (-score, len(path.parts), normalized)


def resolve_texture_path(reference: str, model_path: Path, texture_root=None,
                         recursive_index=None):
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

    if texture_root is not None:
        index = recursive_index or texture_file_index(Path(texture_root))
        indexed_candidates = []
        for name in texture_storage_names(reference):
            indexed_candidates.extend(index.get(Path(name).name.lower(), []))
        for candidate in sorted(
            dict.fromkeys(indexed_candidates),
            key=lambda path: texture_candidate_rank(path, model_path),
        ):
            with candidate.open("rb") as stream:
                if stream.read(4) == b"\x00xet":
                    return candidate
    return None


def image_content_stats(image):
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    if alpha is not None:
        alpha.thumbnail((256, 256))
        if hasattr(alpha, "get_flattened_data"):
            alpha_values = list(alpha.get_flattened_data())
        else:
            alpha_values = list(alpha.getdata())
    else:
        alpha_values = [255]
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
    luminances = [
        (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        for r, g, b in pixels
    ]
    mean_luminance = sum(luminances) / total
    peak_luminance = max(luminances, default=0.0)
    highlight_threshold = mean_luminance + 0.65 * (peak_luminance - mean_luminance)
    highlight_ratio = sum(value >= highlight_threshold for value in luminances) / total
    mean_saturation = sum(
        (max(pixel) - min(pixel)) / max(1, max(pixel)) for pixel in pixels
    ) / total
    return {
        "mean_rgb": means,
        "blue_normal_ratio": blue_ratio,
        "red_mask_ratio": red_mask_ratio,
        "grayscale_ratio": grayscale_ratio,
        "mean_luminance": mean_luminance,
        "peak_luminance": peak_luminance,
        "highlight_ratio": highlight_ratio,
        "mean_saturation": mean_saturation,
        "alpha_min": min(alpha_values),
        "alpha_max": max(alpha_values),
        "transparent_ratio": sum(value < 128 for value in alpha_values)
        / max(1, len(alpha_values)),
    }


def apply_matcap_pbr_estimates(materials):
    """Approximate glTF PBR scalars from a material's reflection matcap.

    A matcap is baked lighting rather than a true parameter map, so the result
    is deliberately tagged as an estimate. Highlight coverage approximates
    microfacet spread (roughness); brightness and chroma approximate the
    strength and tint of the reflected component (metallic).
    """
    for material in materials:
        matcaps = [
            texture for texture in material.get("textures", [])
            if texture.get("role") == "matcap" and texture.get("conversion")
        ]
        if not matcaps:
            continue
        matcap = max(
            matcaps,
            key=lambda texture: (
                texture["conversion"]["width"] * texture["conversion"]["height"]
            ),
        )
        stats = matcap["conversion"]["content"]
        highlight_ratio = stats.get("highlight_ratio", 0.0)
        peak = stats.get("peak_luminance", 0.0)
        mean = stats.get("mean_luminance", 0.0)
        saturation = stats.get("mean_saturation", 0.0)
        contrast = max(0.0, peak - mean)
        # Highlight area behaves more like a lobe area/alpha measurement than
        # glTF's perceptual roughness. Apply the same alpha-to-perceptual
        # square root used for the Phong conversion. A matcap alone cannot
        # distinguish a metal from a strongly reflecting dielectric.
        roughness = min(1.0, max(0.04, highlight_ratio ** 0.25))
        material["pbr_estimate"] = {
            "roughness": roughness,
            "metallic": None,
            "source": "matcap_reflection_estimate",
            "method": "matcap_highlight_perceptual_v2",
            "confidence": min(0.85, max(0.15, contrast)),
            "texture_reference": matcap.get("reference"),
            "measurements": {
                "highlight_ratio": highlight_ratio,
                "mean_luminance": mean,
                "peak_luminance": peak,
                "mean_saturation": saturation,
            },
        }
    return materials


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
            color_first=xet.dxt5_color_first(data),
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
        "dxt5_block_order": (
            "color-alpha" if xet.dxt5_color_first(data) else "alpha-color"
        ) if texture_format == "DXT5" else None,
        "data_offset": offset,
        "content": image_content_stats(image),
    }


def material_mode_of(args) -> str:
    """Return the normalised material output mode ('pbr' or 'godot').

    ``original-godot`` is accepted as a deprecated alias of ``godot`` so that
    existing launch configurations and scripts keep working.
    """
    mode = getattr(args, "material_mode", "pbr") or "pbr"
    if mode == "original-godot":
        return "godot"
    return mode


def uses_godot_materials(args) -> bool:
    """Whether Godot 4 shader/material assets should be emitted."""
    return material_mode_of(args) == "godot"


_NORMAL_SUFFIX_RE = re.compile(r"_n\d*$", re.IGNORECASE)
_COLOR_SUFFIX_RE = re.compile(r"_c\d*$", re.IGNORECASE)
_MATCAP_SUFFIX_RE = re.compile(r"_f\d*$", re.IGNORECASE)
_UTILITY_SUFFIX_RE = re.compile(r"_u\d*$", re.IGNORECASE)


def texture_suffix_role(reference: str) -> str | None:
    """Return the material-role hint encoded in a texture filename suffix.

    The suffix is a *correlation*, not a decoded contract (see
    ``MATERIAL_PBR.md``): ``_c*`` is a base-color candidate, ``_n*`` a normal
    map, ``_f*`` a view-dependent reflection/matcap lookup, and ``_u*`` a
    utility mask. Content statistics still confirm or veto the hint.
    """
    name = reference.replace("\\", "/").rsplit("/", 1)[-1]
    if _MATCAP_SUFFIX_RE.search(name):
        return "matcap"
    if _NORMAL_SUFFIX_RE.search(name):
        return "normal"
    if _UTILITY_SUFFIX_RE.search(name):
        return "utility_mask"
    if _COLOR_SUFFIX_RE.search(name):
        return "diffuse"
    return None


def _content_role(stats: dict) -> str | None:
    """Return the role implied by image content statistics, if any."""
    if stats.get("blue_normal_ratio", 0.0) >= 0.80:
        return "normal"
    if stats.get("red_mask_ratio", 0.0) >= 0.60:
        return "mask"
    return None


def classify_material_textures(textures):
    for texture in textures:
        if not texture.get("conversion"):
            texture["role"] = "unresolved"
            texture["role_source"] = "unresolved"
            continue
        reference = texture.get("reference", "")
        stats = texture["conversion"].get("content", {})
        hint = texture_suffix_role(reference)
        content = _content_role(stats)

        if hint == "matcap":
            # A matcap lookup must never become albedo, even if its content
            # looks flat; the suffix is the only available discriminator.
            texture["role"] = "matcap"
            texture["role_source"] = "suffix"
        elif hint == "normal" or content == "normal":
            texture["role"] = "normal"
            texture["role_source"] = (
                "suffix+content" if (hint == "normal" and content == "normal")
                else ("content" if content == "normal" else "suffix")
            )
        elif content == "mask":
            is_utility = hint == "utility_mask"
            texture["role"] = "utility_mask" if is_utility else "specular_mask"
            texture["role_source"] = "content+suffix" if is_utility else "content"
        elif hint == "utility_mask":
            # Suffix says utility mask but content does not look strongly red:
            # keep the naming hint but record the weaker evidence.
            texture["role"] = "utility_mask"
            texture["role_source"] = "suffix"
        elif hint == "diffuse":
            texture["role"] = "diffuse_candidate"
            texture["role_source"] = "suffix"
        else:
            texture["role"] = "auxiliary"
            texture["role_source"] = "fallback"

    named_diffuse_candidates = [
        texture
        for texture in textures
        if texture.get("role") == "diffuse_candidate" and texture.get("conversion")
    ]
    diffuse_candidates = named_diffuse_candidates or [
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
        named = diffuse.get("role") == "diffuse_candidate"
        diffuse["role"] = "diffuse"
        diffuse["role_source"] = "suffix+size" if named else "size_fallback"
    for texture in named_diffuse_candidates:
        if texture.get("role") == "diffuse_candidate":
            texture["role"] = "auxiliary"
            texture["role_source"] = "demoted"


def resolve_material_textures(materials, model_path, out_dir, texture_root=None):
    texture_dir = out_dir / "textures"
    conversion_cache = {}
    recursive_index = (
        texture_file_index(Path(texture_root)) if texture_root is not None else None
    )
    for material in materials:
        textures = []
        for slot, reference in enumerate(material["texture_references"]):
            source = resolve_texture_path(
                reference, model_path, texture_root, recursive_index,
            )
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


def export_texture_payload(texture_output: Path, texture: dict) -> bytes:
    """Return a PNG payload using glTF/Godot's Y+ normal-map convention."""
    output = texture["output"]
    payload = (texture_output / output).read_bytes()
    if texture.get("role") != "normal":
        return payload

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to convert normal-map handedness.") from exc

    with Image.open(io.BytesIO(payload)) as source:
        image = source.convert("RGBA")
        red, green, blue, alpha = image.split()
        green = green.point(lambda value: 255 - value)
        converted = Image.merge("RGBA", (red, green, blue, alpha))
        destination = io.BytesIO()
        converted.save(destination, format="PNG")
    texture.setdefault("conversion", {})["normal_y_conversion"] = (
        "directx_y_negative_to_gltf_y_positive"
    )
    return destination.getvalue()
