shader_type spatial;
render_mode cull_disabled;

// Thin double-sided cutout cards. The source foliage variant only carries an
// albedo/opacity texture; its generic Phong block is not representative of the
// original leaf lighting. Flip the geometric normal on back faces so both
// visible sides react to the sun from the correct direction.
uniform sampler2D base_texture : source_color, hint_default_white;
uniform vec4 albedo_modulate : source_color = vec4(1.0);

void fragment() {
	vec4 base = texture(base_texture, UV);
	NORMAL = FRONT_FACING ? NORMAL : -NORMAL;
	ALBEDO = base.rgb * COLOR.rgb * albedo_modulate.rgb;
	ALPHA = base.a * COLOR.a;
	ALPHA_SCISSOR_THRESHOLD = 0.5;
	METALLIC = 0.0;
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
}
