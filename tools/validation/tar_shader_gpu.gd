extends RefCounted
## Controlled GPU samples to compare with tar_shader_math.shade_tar.

static func solid(value: Color) -> ImageTexture:
	var image := Image.create(1, 1, false, Image.FORMAT_RGBA8)
	image.fill(value)
	return ImageTexture.create_from_image(image)


static func run(root: Node, variant: int) -> Array:
	var viewport := SubViewport.new()
	viewport.size = Vector2i(32, 32)
	viewport.transparent_bg = true
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(viewport)
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 2.0
	camera.position.z = 2.0
	viewport.add_child(camera)
	camera.make_current()
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	surface.set_color(Color(0.5, 1, 1, 1))
	surface.set_normal(Vector3(0, 0, 1))
	surface.set_tangent(Plane(1, 0, 0, 1))
	surface.set_uv(Vector2(0.5, 0.5))
	for point in [Vector3(-1,-1,0), Vector3(1,1,0), Vector3(1,-1,0), Vector3(-1,-1,0), Vector3(-1,1,0), Vector3(1,1,0)]:
		surface.add_vertex(point)
	var mesh := MeshInstance3D.new()
	mesh.mesh = surface.commit()
	var material := ShaderMaterial.new()
	material.shader = load("res://majin_utility/player_darkness.gdshader")
	mesh.material_override = material
	viewport.add_child(mesh)
	var cube := Cubemap.new()
	var faces: Array[Image] = []
	for face in range(6):
		faces.append(solid(Color(1,0,0,1)).get_image())
	cube.create_from_images(faces)
	var uniforms := {
		"tar_variant": variant,
		"normal_texture": solid(Color(128.0/255.0,128.0/255.0,1,1)),
		"tar_normal_texture": solid(Color(140.0/255.0,116.0/255.0,250.0/255.0,1)),
		"utility_texture": solid(Color(64.0/255.0,0,0,1)),
		"tar_noise_texture": solid(Color(51.0/255.0,0,0,1)),
		"env_sphere_texture": solid(Color(0,1,0,1)),
		"env_cube_texture": cube,
		"shadow_texture": solid(Color(128.0/255.0,0,0,1)),
		"use_normal_texture": true, "use_utility_texture": true,
		"use_tar_normal_texture": true, "use_tar_noise_texture": true,
		"use_env_sphere_texture": true, "use_shadow_texture": true,
		"mat_param0": Vector4(16,0.01,0.03,0.01),
		"mat_param1": Vector4(0.5,0,1,1), "mat_param2": Vector4(0,0.1,0.1,0),
		"color_sky": Vector3(0.06,0.07,0.08), "color_ground": Vector3(0.01,0.02,0.03),
		"hemisphere_axis": Vector3(0,0,-1),
		"dir_light_vector": Vector3(0.3,0.4,1), "color_dir_light": Vector3(0.2,0.18,0.15),
		"specular_color": Vector3(0.03,0.02,0.01),
		"point_light_position": PackedVector3Array([Vector3(1,1,3),Vector3(-1,0,2),Vector3(0,1,1)]),
		"point_light_color": PackedVector3Array([Vector3(0.05,0.04,0.03),Vector3(0.02,0.03,0.04),Vector3(0.04,0.02,0.03)]),
		"point_light_param": PackedVector2Array([Vector2(50,500),Vector2(20,400),Vector2(10,300)]),
		"fog_param": Vector3(0.002,500,0.002), "fog_color": Vector3(0.01,0.02,0.03),
		"output_gain": 0.7,
	}
	for key in uniforms:
		material.set_shader_parameter(key, uniforms[key])
	mesh.set_instance_shader_parameter("tar_hp",Vector3(1,0.4,5))
	# Vector4 keeps this a numerical input rather than an editor color swatch.
	mesh.set_instance_shader_parameter("tar_color",Vector4(0.2,0.3,0.4,0.6))
	for frame in range(6):
		await root.get_tree().process_frame
		await RenderingServer.frame_post_draw
	var samples := []
	for lights in range(4):
		for cube_mode in range(2):
			for fog in range(3):
				for alpha in [0.0,192.0/255.0]:
					material.set_shader_parameter("point_light_count",lights)
					material.set_shader_parameter("use_env_cube_texture",cube_mode == 1)
					material.set_shader_parameter("fog_select",Vector3(float(fog==0),float(fog==1),float(fog==2)))
					material.set_shader_parameter("base_texture",solid(Color(1,1,1,alpha)))
					for frame in range(3):
						await root.get_tree().process_frame
						await RenderingServer.frame_post_draw
					var pixel := viewport.get_texture().get_image().get_pixel(16,16)
					samples.append({"variant":variant,"lights":lights,"cube":cube_mode,"fog":fog,"base_alpha":alpha,"rgba":[pixel.r,pixel.g,pixel.b,pixel.a]})
	viewport.queue_free()
	return samples


static func run_shadow_test(root: Node) -> Dictionary:
	var viewport := SubViewport.new()
	viewport.size = Vector2i(128,128)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	viewport.positional_shadow_atlas_size = 512
	root.add_child(viewport)
	var camera := Camera3D.new()
	camera.position.z = 3.0
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 2.0
	camera.far = 10.0
	viewport.add_child(camera)
	camera.make_current()
	var light := DirectionalLight3D.new()
	light.shadow_enabled = true
	light.directional_shadow_max_distance = 10.0
	viewport.add_child(light)
	var receiver := MeshInstance3D.new()
	var quad := QuadMesh.new()
	quad.size = Vector2(2,2)
	receiver.mesh = quad
	var material := ShaderMaterial.new()
	material.shader = load("res://majin_utility/player_darkness.gdshader")
	material.set_shader_parameter("use_scene_lights",true)
	material.set_shader_parameter("color_sky",Vector3.ZERO)
	material.set_shader_parameter("color_ground",Vector3.ZERO)
	material.set_shader_parameter("specular_color",Vector3.ZERO)
	receiver.material_override = material
	viewport.add_child(receiver)
	receiver.set_instance_shader_parameter("tar_color",Vector4(1,1,1,1))
	var blocker := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(0.5,0.5,0.1)
	blocker.mesh = box
	blocker.position.z = 1.0
	blocker.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_SHADOWS_ONLY
	viewport.add_child(blocker)
	for frame in range(8):
		await root.get_tree().process_frame
		await RenderingServer.frame_post_draw
	var shadowed := viewport.get_texture().get_image().get_pixel(64,64).r
	var outside := viewport.get_texture().get_image().get_pixel(16,16).r
	blocker.visible = false
	for frame in range(4):
		await root.get_tree().process_frame
		await RenderingServer.frame_post_draw
	var lit := viewport.get_texture().get_image().get_pixel(64,64).r
	viewport.queue_free()
	return {"shadowed":shadowed,"outside_shadow":outside,"lit":lit,"passed":lit-shadowed > 0.2 and outside > 0.5}


static func run_overlay_depth_test(root: Node) -> Dictionary:
	# A clean surface just in front of tar must not leak through opaque tar.
	# A separate, much closer object must still occlude it; the edge stays soft.
	var viewport := SubViewport.new()
	viewport.size = Vector2i(32,32)
	viewport.own_world_3d = true
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(viewport)
	var camera := Camera3D.new()
	camera.position.z = 2.0
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 2.0
	viewport.add_child(camera)
	camera.make_current()
	var clean := MeshInstance3D.new()
	clean.mesh = QuadMesh.new()
	var green := StandardMaterial3D.new()
	green.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	green.albedo_color = Color.GREEN
	clean.material_override = green
	viewport.add_child(clean)
	var tar := MeshInstance3D.new()
	tar.mesh = QuadMesh.new()
	tar.position.z = -0.0005
	var material := ShaderMaterial.new()
	material.shader = load("res://majin_utility/player_darkness.gdshader")
	material.set_shader_parameter("color_sky",Vector3.ONE)
	material.set_shader_parameter("color_ground",Vector3.ONE)
	material.set_shader_parameter("color_dir_light",Vector3.ZERO)
	material.set_shader_parameter("specular_color",Vector3.ZERO)
	tar.material_override = material
	viewport.add_child(tar)
	tar.set_instance_shader_parameter("tar_color",Vector4(1,0,0,1))
	var results := {}
	for state in ["no_bias", "opaque", "edge", "foreground"]:
		tar.set_instance_shader_parameter("tar_depth_bias",0.0 if state == "no_bias" else 0.001)
		tar.set_instance_shader_parameter("tar_hp",Vector3(1,0,0.5) if state == "edge" else Vector3(1,1,1))
		clean.position.z = 0.05 if state == "foreground" else 0.0
		for frame in range(4):
			await root.get_tree().process_frame
			await RenderingServer.frame_post_draw
		var pixel := viewport.get_texture().get_image().get_pixel(16,16)
		results[state] = [pixel.r,pixel.g,pixel.b,pixel.a]
	results["passed"] = results.no_bias[1] > 0.9 and results.opaque[0] > 0.9 \
		and results.opaque[1] < 0.05 and results.foreground[1] > 0.9 \
		and results.edge[0] > 0.5 and results.edge[1] > 0.5
	viewport.queue_free()
	return results
