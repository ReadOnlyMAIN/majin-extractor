
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
