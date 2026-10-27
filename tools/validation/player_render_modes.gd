extends RefCounted
## Integration checks against the actual imported bodies and accessories.

static func run(lab: Node) -> Dictionary:
	var player: Node3D = lab.model
	var animation_player: AnimationPlayer = player.get_animation_player()
	var clip := animation_player.current_animation
	var time := animation_player.current_animation_position
	var saved_body: String = player.body
	var saved_head: String = player.head
	var saved_bracelets: String = player.bracelets
	var saved_mode: bool = player.cinematic_render
	var saved_health: float = player.health_ratio
	var failures: Array[String] = []
	var bodies := 0
	var accessories := 0
	player.health_ratio = 0.25
	for body in player.available_equipment("body"):
		player.body = body
		for mode in [false, true]:
			player.cinematic_render = mode
			var expected: String = body if mode else player.ARMOR_PAIRS[body]
			if player._pieces["body"].scene_file_path.get_file() != expected + ".glb":
				failures.append("Wrong body family: " + body)
			if player.get_animation_player() != animation_player or animation_player.current_animation != clip or abs(animation_player.current_animation_position-time)>0.0001:
				failures.append("Animation changed when switching " + body)
			for mesh in player._tar_meshes():
				if mesh.is_visible_in_tree() == mode:
					failures.append("Wrong tar visibility: " + body)
			bodies += 1
			await lab.get_tree().process_frame
	for slot in ["head", "bracelets"]:
		for asset in player.available_equipment(slot):
			player.set(slot, asset)
			if asset == "none":
				continue
			var keys: Array = ["head"] if slot == "head" else ["bracelets_positive", "bracelets_negative"]
			for key in keys:
				if not player._pieces.has(key):
					failures.append("Missing accessory: " + asset)
					continue
				var piece: Node3D = player._pieces[key]
				if not piece.transform.is_finite():
					failures.append("Invalid attachment transform: " + asset)
				for mesh in piece.find_children("*", "MeshInstance3D", true, false):
					if not mesh.get_meta("player_equipped_surface", true):
						if mesh.is_visible_in_tree(): failures.append("Wrong bracelet half visible")
						continue
					for i in range(mesh.mesh.get_surface_count()):
						var material = mesh.get_active_material(i)
						if material == null or (not material is ShaderMaterial and not material.resource_path.contains("/materials/")):
							failures.append("Missing source material: " + asset)
			var manifest_path: String = player._catalog[asset].get_base_dir().path_join("materials/material_bindings.json")
			var manifest: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
			for entry in manifest.get("materials", []):
				for texture_path in entry.get("uniforms", {}).values():
					if not ResourceLoader.exists(player._catalog[asset].get_base_dir().path_join(texture_path)):
						failures.append("Missing texture: " + texture_path)
			accessories += 1
			await lab.get_tree().process_frame
	player.body = saved_body
	player.head = saved_head
	player.bracelets = saved_bracelets
	player.cinematic_render = saved_mode
	player.health_ratio = saved_health
	lab._setup_customization()
	return {"body_mode_cases":bodies,"accessories":accessories,"animations":animation_player.get_animation_list().size(),"failures":failures,"passed":failures.is_empty()}
