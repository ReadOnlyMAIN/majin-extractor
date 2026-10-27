shader_type spatial;
render_mode depth_prepass_alpha, ambient_light_disabled, fog_disabled;

// Parameterized KbTarModel fragment equations; Godot supplies skinning and projection.
// Unknown engine globals are optional inputs, never guessed from texture names.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform sampler2D normal_texture : repeat_enable, filter_linear_mipmap;
uniform sampler2D utility_texture : hint_default_white;
uniform sampler2D env_sphere_texture : source_color, hint_default_black, repeat_disable;
uniform sampler2D tar_normal_texture : repeat_enable, filter_linear_mipmap;
uniform sampler2D tar_noise_texture : hint_default_white, repeat_enable, filter_linear_mipmap;
uniform samplerCube env_cube_texture : source_color, hint_default_black;
uniform sampler2D shadow_texture : hint_default_white, repeat_disable;
uniform bool use_shadow_texture = false;
// Replay mode uses the explicit original lights. Scene mode also receives
// Godot shadow maps, with Godot's attenuation for scene point lights.
uniform bool use_scene_lights = false;
varying float fragment_utility;
varying float fragment_fog;
// Inputs equivalent to the original frame uniforms. World distances use meters.
uniform int tar_variant : hint_range(0, 7) = 0;
uniform bool use_env_cube_texture = false;
uniform vec3 hemisphere_axis = vec3(0.0, -1.0, 0.0);
uniform vec3 color_sky = vec3(0.55, 0.60, 0.70);
uniform vec3 color_ground = vec3(0.12, 0.10, 0.09);
uniform vec3 dir_light_vector = vec3(0.3, 0.7, 1.0);
uniform vec3 color_dir_light = vec3(1.0);
uniform vec3 specular_color = vec3(1.0);
uniform int point_light_count : hint_range(0, 3) = 0;
uniform vec3 point_light_position[3];
uniform vec3 point_light_color[3];
// Source pLightParam.y/w, in centimeters (x/z are unused).
uniform vec2 point_light_param[3];
uniform vec3 fog_param = vec3(0.0);
uniform vec3 fog_select = vec3(0.0, 1.0, 0.0);
uniform vec3 fog_color = vec3(0.0);
uniform float output_gain = 1.0;
uniform bool use_normal_texture = false;
uniform bool normal_texture_y_converted = false;
uniform bool use_utility_texture = false;
uniform bool use_env_sphere_texture = false;
uniform bool use_tar_normal_texture = false;
uniform bool use_tar_noise_texture = false;
uniform float roughness : hint_range(0.0, 1.0) = 0.3;
uniform float specular : hint_range(0.0, 1.0) = 0.5;
// Material/frame values remain explicit; defaults are not a captured game frame.
uniform vec4 mat_param0 = vec4(24.0, 1.0, 1.0, 0.4);
uniform vec4 mat_param1 = vec4(0.5, 0.0, 1.0, 1.0);
uniform vec4 mat_param2 = vec4(0.0);
instance uniform vec3 tar_hp = vec3(1.0, 1.0, 1.0);
instance uniform vec4 tar_color : source_color = vec4(0.02, 0.015, 0.025, 1.0);
// Godot overlay depth separation, in meters; zero preserves source projection.
instance uniform float tar_depth_bias = 0.0;
instance uniform vec3 tar_root_world = vec3(0.0);
instance uniform vec4 tar_uv_offset = vec4(0.0);
instance uniform float tar_normal_strength = 1.0;
instance uniform float tar_reflection_strength = 1.0;
instance uniform bool tar_show_mask = false;
varying vec2 tar_uv;
varying vec3 source_camera_delta;
varying vec3 source_view;
varying vec3 source_hemi;
varying vec3 source_light;
varying vec3 source_point0;
varying vec3 source_point1;
varying vec3 source_point2;

vec3 tangent_vector(vec3 v, vec3 t, vec3 b, vec3 n) {
    return vec3(dot(v,t), dot(v,b), dot(v,n));
}

float source_angle(vec2 n) {
    vec2 a = abs(n);
    // Defined fallback for the original reciprocal singularity at the poles.
    float q = min(a.x, a.y) / max(max(a.x, a.y), 0.000001);
    float t = q*q;
    float angle = (((((-0.01348114013671875*t + 0.057464599609375)*t
        - 0.1212158203125)*t + 0.1956787109375)*t
        - 0.3330078125)*t + 1.0)*q;
    if (a.y > a.x) { angle = 1.5703125 - angle; }
    if (n.x < 0.0) { angle = 3.140625 - angle; }
    if (n.y < 0.0) { angle = -angle; }
    return angle;
}

void vertex() {
    // Separate almost coincident clean/tar surfaces without inflating the
    // silhouette or changing the original position used by UVs and lighting.
    vec4 view_position = MODELVIEW_MATRIX * vec4(VERTEX, 1.0);
    vec4 clip_position = PROJECTION_MATRIX * view_position;
    vec4 biased_clip = PROJECTION_MATRIX
        * vec4(view_position.xyz + vec3(0.0, 0.0, tar_depth_bias), 1.0);
    clip_position.z = biased_clip.z * clip_position.w / biased_clip.w;
    POSITION = clip_position;
    // Exported models use meters; the source UV equation used centimeters.
    vec3 position_world = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    vec3 p = (position_world - tar_root_world) * 100.0;
    vec3 n = normalize(MODEL_NORMAL_MATRIX * NORMAL);
    vec3 t = normalize((MODEL_MATRIX * vec4(TANGENT, 0.0)).xyz) * mat_param1.z;
    vec3 b = normalize((MODEL_MATRIX * vec4(BINORMAL, 0.0)).xyz) * mat_param1.w;
    source_camera_delta = (INV_VIEW_MATRIX[3].xyz-position_world)*100.0;
    source_view = tangent_vector(-source_camera_delta,t,b,n);
    source_hemi = tangent_vector(-hemisphere_axis,t,b,n);
    source_light = tangent_vector(dir_light_vector,t,b,n);
    source_point0 = tangent_vector((point_light_position[0]-position_world)*100.0,t,b,n);
    source_point1 = tangent_vector((point_light_position[1]-position_world)*100.0,t,b,n);
    source_point2 = tangent_vector((point_light_position[2]-position_world)*100.0,t,b,n);
    tar_uv = vec2(0.012500000186264515*(p.x+p.z)
        + (3.1414999961853027+source_angle(n.xz))*0.31831926107406616,
        p.y*0.03333333507180214) + tar_uv_offset.zw;
    if (tar_variant == 3 || tar_variant == 6) { tar_uv = UV; }
}

void fragment() {
    float mask = clamp(COLOR.r, 0.0, 1.0);
    vec2 uv = (tar_variant == 3 || tar_variant == 6) ? UV : UV + tar_uv_offset.xy;
    vec4 base = texture(base_texture, uv);
    bool progressive = tar_variant == 0 || tar_variant == 7;
    bool hybrid = tar_variant == 2 || tar_variant == 5;
    if (!tar_show_mask && ((!hybrid && base.a < 0.01) || (progressive && mask > tar_hp.x))) { discard; }
    ALPHA = tar_show_mask ? 1.0 : (progressive ? (mask > tar_hp.y
        ? 1.0 - (mask-tar_hp.y)*tar_hp.z : 1.0) : tar_color.a);
    float tar_weight = hybrid ? base.a : 1.0;

    vec3 sampled_base_normal = texture(normal_texture,uv).rgb;
    // The ordinary glTF/Godot exporter flips green for built-in NORMAL_MAP.
    // Recover the source channels before evaluating the original tar equations.
    if (normal_texture_y_converted) { sampled_base_normal.g = 1.0-sampled_base_normal.g; }
    vec3 nb = use_normal_texture
        ? normalize(sampled_base_normal*2.0-1.0) : vec3(0.0,0.0,1.0);
    float perturb_b = 0.0;
    float perturb_t = 0.0;
    if (use_tar_noise_texture) {
        perturb_b = mat_param2.z * sin(6.2829999923706055
            * texture(tar_noise_texture, uv + vec2(mat_param2.y,0.0)).r);
        perturb_t = mat_param2.z * sin(6.2829999923706055
            * texture(tar_noise_texture, tar_uv + vec2(mat_param2.y,0.0)).r);
    }
    vec2 bxy = nb.xy * (mat_param1.x + perturb_b);
    // Guard sqrt domain when users supply parameters outside the game range.
    vec3 mixed_normal = normalize(vec3(bxy, sqrt(max(0.000001,1.0000100135803223-dot(bxy,bxy)))));
    if (use_tar_normal_texture) {
        vec3 nt = normalize(texture(tar_normal_texture, tar_uv).rgb*2.0-1.0);
        vec2 txy = nt.xy * (0.5+perturb_t);
        vec3 tar_n = normalize(vec3(txy, sqrt(max(0.000001,1.0-dot(txy,txy)))));
        mixed_normal = normalize(mixed_normal + tar_weight*tar_n);
    }
    mixed_normal = normalize(mix(vec3(0.0,0.0,1.0),mixed_normal,tar_normal_strength));
    vec3 n = mixed_normal;
    vec3 v = normalize(source_view);
    vec3 hemi = normalize(source_hemi);
    vec3 light = normalize(source_light);
    float shadow = use_shadow_texture ? texture(shadow_texture, SCREEN_UV
        + vec2(0.0007812500116415322,0.0013888999819755554)).r : 1.0;
    float direct = max(dot(n,light),0.0)*shadow;
    vec3 lighting = mix(color_ground,color_sky,0.5*dot(n,hemi)+0.5)
        + color_dir_light*direct;
    float phong = pow(max(dot(n,normalize(light-v)),0.0),mat_param0.x)*direct;
    vec3 hemisphere = mix(color_ground,color_sky,0.5*dot(n,hemi)+0.5);
    for (int i=0; i<3; i++) {
        if (i >= point_light_count) { break; }
        vec3 point_delta = i == 0 ? source_point0 : (i == 1 ? source_point1 : source_point2);
        float distance_to_light = length(point_delta);
        vec3 point_direction = normalize(point_delta);
        float attenuation = clamp((point_light_param[i].y-distance_to_light+point_light_param[i].x)
            / max(point_light_param[i].y,0.000001),0.0,1.0);
        float diffuse = max(dot(n,point_direction),0.0)*attenuation;
        lighting += point_light_color[i]*diffuse;
        phong = max(phong,pow(max(dot(n,normalize(point_direction-v)),0.0),mat_param0.x)*diffuse);
    }
    float fresnel = 1.0-abs(dot(n,v));
    float reflection_weight = mix(mat_param0.y,mat_param0.z,fresnel);
    vec3 r = 2.0*n*dot(n,v)-v;
    vec3 reflected = -r;
    vec2 sphere_uv = vec2(0.5) + vec2(reflected.x,-reflected.y)
        / max(2.0*length(reflected+vec3(0.0,0.0,1.0)),0.000001);
    float utility = use_utility_texture ? texture(utility_texture,uv).r : 1.0;
    vec3 environment = use_env_cube_texture ? texture(env_cube_texture,r).rgb
        : (use_env_sphere_texture ? texture(env_sphere_texture,sphere_uv).rgb : vec3(0.0));
    float edge_specular = mat_param0.w*pow(fresnel,3.0)*pow(max(dot(v,light),0.0),2.0);
    float spec = utility*phong + edge_specular;
    vec3 albedo = hybrid ? mix(base.rgb,tar_color.rgb,base.a) : tar_color.rgb;
    vec3 color = albedo*lighting + specular_color*spec
        + tar_weight*utility*reflection_weight*environment*tar_reflection_strength;
    float distance_to_camera = length(source_camera_delta);
    float density_distance = fog_param.z*distance_to_camera;
    vec3 fog_candidates = vec3((fog_param.y-distance_to_camera)*fog_param.x,
        exp2(-density_distance*1.4426950216293335),
        exp2(-density_distance*density_distance*1.4426950216293335));
    float fog = clamp(dot(fog_candidates,fog_select),0.0,1.0);
    fragment_utility = utility;
    fragment_fog = fog;
    NORMAL = normalize(TANGENT*mixed_normal.x*mat_param1.z
        + BINORMAL*mixed_normal.y*mat_param1.w + NORMAL*mixed_normal.z);
    ALBEDO = use_scene_lights && !tar_show_mask ? albedo : vec3(0.0);
    vec3 indirect = albedo*hemisphere + specular_color*edge_specular
        + tar_weight*utility*reflection_weight*environment*tar_reflection_strength;
    EMISSION = tar_show_mask ? vec3(mask)
        : mix(fog_color,use_scene_lights ? indirect : color,fog)*output_gain;
}

void light() {
    if (use_scene_lights && !tar_show_mask) {
        float direct = max(dot(NORMAL,LIGHT),0.0)*ATTENUATION;
        DIFFUSE_LIGHT += (LIGHT_COLOR/PI)*direct*fragment_fog*output_gain;
        float phong = pow(max(dot(NORMAL,normalize(LIGHT+VIEW)),0.0),mat_param0.x)*direct;
        // The original keeps the largest specular contribution across lights.
        SPECULAR_LIGHT = max(SPECULAR_LIGHT,
            specular_color*fragment_utility*phong*fragment_fog*output_gain);
    }
}
