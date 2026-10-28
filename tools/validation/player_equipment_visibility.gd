extends RefCounted
## Check the actual imported accessory shaders, health uniforms and hair groups.

static func run(lab: Node) -> Dictionary:
	var p: Node = lab.model
	var saved := {}
	for key in ["body", "head", "bracelets", "cinematic_render", "health_ratio", "head_hair_mode"]:
		saved[key] = p.get(key)
	var failures: Array[String] = []
	var hair_cases := 0
	var health_cases := 0
	p.head_hair_mode = 0
	for body in p.available_equipment("body"):
		p.body = body
		for mode in [false, true]:
			p.cinematic_render = mode
			for head in ["none", "chr720", "chr721", "chr722", "chr723", "chr724", "chr725", "chr726"]:
				p.head = head
				var hidden: int = p.HEAD_HAIR_COVERAGE.get(head, 0)
				for health in [1.0, 0.5, 0.0]:
					p.health_ratio = health
					for mesh in p._pieces["body"].find_children("*", "MeshInstance3D", true, false):
						var mask := 1 if String(mesh.name).begins_with("armor_hair_palm") else (2 if String(mesh.name).begins_with("armor_hair_fringe") else 0)
						if mask == 0:
							continue
						var visible: bool = (mask & hidden) == 0 and (not String(mesh.name).ends_with("_darkness") or (not mode and health < 1.0))
						if mesh.is_visible_in_tree() != visible:
							failures.append("Hair visibility: %s/%s" % [body, head])
					hair_cases += 1
				await lab.get_tree().process_frame
	p.body = "chr101"
	p.cinematic_render = false
	for slot in ["head", "bracelets"]:
		for asset in p.available_equipment(slot):
			if asset == "none" or not p.equipment_source_note(asset).is_empty():
				continue
			p.set(slot, asset)
			var keys: Array = ["head"] if slot == "head" else ["bracelets_positive", "bracelets_negative"]
			for key in keys:
				var tar_surfaces := 0
				for mesh in p._pieces[key].find_children("*", "MeshInstance3D", true, false):
					if not String(mesh.name).ends_with("_darkness") or not mesh.get_meta("player_equipped_surface", true):
						continue
					for surface in range(mesh.mesh.get_surface_count()):
						var material = mesh.get_active_material(surface)
						if not material is ShaderMaterial or not material.shader.resource_path.ends_with("player_darkness.gdshader"):
							failures.append("Wrong accessory shader: " + asset)
						else:
							if material.get_shader_parameter("tar_normal_texture") != p.darkness_tar_normal:
								failures.append("Missing flow texture: " + asset)
						tar_surfaces += 1
					for health in [1.0, 0.8, 0.5, 0.2, 0.0]:
						p.health_ratio = health
						var width: float = p.darkness_mask_feather
						var cutoff: float = (1.0 - health) * (1.0 + width)
						var expected := Vector3(cutoff, cutoff - width, 1.0 / width)
						if not mesh.get_instance_shader_parameter("tar_hp").is_equal_approx(expected) or mesh.is_visible_in_tree() != (health < 1.0):
							failures.append("Accessory health response: " + asset)
						health_cases += 1
				if tar_surfaces == 0:
					failures.append("Missing accessory tar surface: " + asset)
	p.head = "chr720"
	p.head_hair_mode = 1
	for mesh in p._pieces["body"].find_children("*", "MeshInstance3D", true, false):
		if String(mesh.name).begins_with("armor_hair_") and not mesh.get_meta("player_hair_visible", true):
			failures.append("Show-all override did not restore hair")
	for key in saved:
		p.set(key, saved[key])
	lab._setup_customization()
	return {"passed":failures.is_empty(),"failures":failures,"hair_health_mode_cases":hair_cases,"accessory_health_cases":health_cases}
