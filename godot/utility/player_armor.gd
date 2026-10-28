@tool
extends Node3D
## Gameplay/cinematic body families decoded from the original costume tables.
## Visible bodies retain their complete geometry and their own tar surfaces.

@export_dir var characters_directory := "res://original_files/decoded/chara"
@export var cinematic_render := true:
	set(value):
		cinematic_render = value
		if is_node_ready():
			_equip_body()
@export var body := "chr101":
	set(value):
		body = value
		if is_node_ready():
			_equip_body()
@export var head := "none":
	set(value):
		head = value
		if is_node_ready():
			_equip_accessory("head", value)
## Automatic coverage is a Godot preview policy, not a decoded original switch.
@export_enum("Automatic", "Show all", "Hide palm", "Hide fringe", "Hide both") var head_hair_mode := 0:
	set(value):
		head_hair_mode = value
		if is_node_ready():
			_refresh_hair_visibility()
@export var bracelets := "none":
	set(value):
		bracelets = value
		if is_node_ready():
			_equip_accessory("bracelets", value)

# Cinematic -> gameplay, including the three additional source costume pairs.
const ARMOR_PAIRS := {
	"chr101": "chr100", "chr111": "chr110", "chr116": "chr115",
	"chr121": "chr120", "chr126": "chr125", "chr131": "chr130",
	"chr136": "chr135", "chr141": "chr140", "chr151": "chr150",
	"chr156": "chr155", "chr146": "chr145",
}

# Coverage observed from the accessory geometry; crowns leave the hair exposed.
const HEAD_HAIR_COVERAGE := {
	"chr720": 3, "chr721": 1, "chr722": 1, "chr723": 3,
	"chr725": 3, "chr726": 2,
}
# Exact gray source textures, confirmed against the original XET files.
const SOURCE_PLACEHOLDER_NOTES := {
	"chr145": "Original costume texture is uniform gray (128, 128, 128).",
	"chr146": "Original costume texture is uniform gray (128, 128, 128).",
	"chr150": "Original clothing texture is uniform gray; skin and hair textures exist.",
	"chr151": "Original clothing texture is uniform gray; skin and hair textures exist.",
	"chr155": "Original clothing texture is uniform gray; skin and hair textures exist.",
	"chr156": "Original clothing texture is uniform gray; skin and hair textures exist.",
}

## Health-to-mask mapping is a preview; the game's CPU PV->tarHP curve is unknown.
@export_range(0.0, 1.0, 0.01) var health_ratio := 1.0:
	set(value):
		health_ratio = clampf(value, 0.0, 1.0)
		_refresh_darkness()
## Small depth separation for coincident clean/tar geometry, in meters.
@export_range(0.0, 0.01, 0.0001) var darkness_depth_bias := 0.0:
	set(value):
		darkness_depth_bias = value
		_refresh_darkness()
## Width in vertex-mask units, not meters.
@export_range(0.001, 1.0, 0.001) var darkness_mask_feather := 0.04:
	set(value):
		darkness_mask_feather = value
		_refresh_darkness()
## Enable to inspect the original shader inputs independently of preview health.
@export var use_original_tar_hp := false:
	set(value):
		use_original_tar_hp = value
		_refresh_darkness()
@export var tar_hp := Vector3(1.0, 1.0, 1.0):
	set(value):
		tar_hp = value
		_refresh_darkness()

@export var darkness_color := Color(0.02, 0.015, 0.025, 1.0):
	set(value):
		darkness_color = value
		_refresh_darkness()
@export_range(0.0, 2.0, 0.01) var darkness_reflection := 1.0:
	set(value):
		darkness_reflection = value
		_refresh_darkness()
@export_range(0.0, 2.0, 0.01) var darkness_normal_strength := 1.0:
	set(value):
		darkness_normal_strength = value
		_refresh_darkness()
@export var darkness_show_mask := false:
	set(value):
		darkness_show_mask = value
		_refresh_darkness()
## Scrolling is a preview speed; original animated uvOffset is unknown.
@export var darkness_animate := true
# Visual reference: at least 0.1. Source constructor 0.006 is not the final phase speed.
@export_range(0.0, 2.0, 0.001) var darkness_flow_speed := 0.1
@export_range(-360.0, 360.0, 1.0) var darkness_flow_angle := 90.0
## Scene integration receives Godot shadows. Disable for original-uniform replay.
@export var darkness_scene_lighting := true:
	set(value):
		darkness_scene_lighting = value
		_refresh_tar_textures()
## Optional original global samplers. Unset means disabled, no guessed binding.
@export var darkness_tar_normal: Texture2D:
	set(value):
		darkness_tar_normal = value
		_refresh_tar_textures()
@export var darkness_noise: Texture2D:
	set(value):
		darkness_noise = value
		_refresh_tar_textures()
@export_range(0.0, 0.5, 0.01) var darkness_noise_strength := 0.15:
	set(value):
		darkness_noise_strength = value
		_refresh_tar_textures()
var _tar_phase := Vector2.ZERO
var _tar_lighting: Dictionary = {}


var _base: Node3D
var _skeleton: Skeleton3D
var _pieces: Dictionary = {}
var _catalog: Dictionary = {}


func _ready() -> void:
	for folder in DirAccess.get_directories_at(characters_directory):
		if folder.length() == 6 and (folder.begins_with("chr1") or folder.begins_with("chr72") or folder.begins_with("chr74")) and folder.substr(3).is_valid_int():
			var path := characters_directory.path_join(folder).path_join(folder + ".glb")
			if ResourceLoader.exists(path):
				_catalog[folder] = path
	notify_property_list_changed()
	if not _catalog.has("chr100"):
		push_warning("Player armor requires an exported chr100 in " + characters_directory)
		return
	_load_tar_globals()
	# The serialized rig exposes its AnimationPlayer to AnimationTree's editor.
	# Keep the fallback for scenes generated before AnimationRig was included.
	_base = get_node_or_null("AnimationRig") as Node3D
	if _base == null:
		_base = (load(_catalog["chr100"]) as PackedScene).instantiate()
		_base.name = "AnimationRig"
		add_child(_base)
	_skeleton = _find_skeleton(_base)
	for mesh in _base.find_children("*", "MeshInstance3D", true, false):
		mesh.hide()
	if _skeleton == null:
		push_error("chr100 has no Skeleton3D")
		return
	_skeleton.skeleton_updated.connect(_sync_poses)
	_equip_body()
	_equip_accessory("head", head)
	_equip_accessory("bracelets", bracelets)
	_refresh_darkness.call_deferred()


func _validate_property(property: Dictionary) -> void:
	if property.name in ["body", "head", "bracelets"]:
		property.hint = PROPERTY_HINT_ENUM
		property.hint_string = ",".join(available_equipment(property.name))


func available_equipment(slot: String) -> Array[String]:
	var names: Array[String] = []
	if slot == "body":
		for asset in ARMOR_PAIRS:
			if _catalog.has(asset) and _catalog.has(ARMOR_PAIRS[asset]):
				names.append(asset)
	else:
		names.append("none")
		var prefix := "chr72" if slot == "head" else "chr74"
		for asset in _catalog:
			if String(asset).begins_with(prefix):
				names.append(asset)
	return names


func available_armor() -> Array[String]:
	return available_equipment("body")


func get_animation_player() -> AnimationPlayer:
	var rig := _base if is_instance_valid(_base) else get_node_or_null("AnimationRig")
	if rig == null:
		return null
	return rig.find_child("AnimationPlayer", true, false) as AnimationPlayer


func get_animation_tree() -> AnimationTree:
	return get_node_or_null("AnimationTree") as AnimationTree


func equipment_source_note(asset: String) -> String:
	if asset in ["chr727", "chr728", "chr729", "chr747", "chr748", "chr749"]:
		return "Original test mesh: 24 vertices, with a uniform gray texture named test."
	return SOURCE_PLACEHOLDER_NOTES.get(asset, "")


func _find_skeleton(root: Node) -> Skeleton3D:
	if root is Skeleton3D:
		return root as Skeleton3D
	for child in root.get_children():
		var found := _find_skeleton(child)
		if found != null:
			return found
	return null


func _remove_piece(key: String) -> void:
	if _pieces.has(key):
		var previous: Node = _pieces[key]
		_pieces.erase(key)
		remove_child(previous)
		previous.queue_free()


func _equip_body() -> void:
	if not is_instance_valid(_skeleton):
		return
	var selected := body
	for clean in ARMOR_PAIRS:
		if ARMOR_PAIRS[clean] == selected:
			selected = clean
	if not ARMOR_PAIRS.has(selected):
		push_warning("Unknown body costume: " + body)
		return
	var asset: String = selected if cinematic_render else ARMOR_PAIRS[selected]
	if not _catalog.has(asset):
		push_warning("Missing body model: " + asset)
		return
	var piece := _new_piece(asset)
	_remove_piece("body")
	_pieces["body"] = piece
	add_child(piece)
	_prepare_tar_materials(piece)
	_refresh_hair_visibility()
	_refresh_darkness()
	_refresh_tar_textures()
	_sync_poses()


func _equip_accessory(slot: String, asset: String) -> void:
	for key in _pieces.keys():
		if key == slot or String(key).begins_with(slot + "_"):
			_remove_piece(key)
	if asset == "none" or not is_instance_valid(_skeleton):
		_refresh_hair_visibility()
		return
	var prefix := "chr72" if slot == "head" else "chr74"
	if not asset.begins_with(prefix) or not _catalog.has(asset):
		push_warning("Missing " + slot + " accessory: " + asset)
		_refresh_hair_visibility()
		return
	var sides := [""] if slot == "head" else ["positive", "negative"]
	for side in sides:
		var piece := _new_piece(asset)
		var key: String = slot if side == "" else slot + "_" + side
		_pieces[key] = piece
		add_child(piece)
		for mesh in piece.find_children("*", "MeshInstance3D", true, false):
			var selected: bool = side == "" or String(mesh.name).begins_with("armor_bracelet_" + side)
			mesh.visible = selected
			mesh.set_meta("player_equipped_surface", selected)
		_prepare_tar_materials(piece)
	_refresh_hair_visibility()
	_refresh_tar_textures()
	_refresh_darkness()
	_sync_poses()


func _new_piece(asset: String) -> Node3D:
	var piece := (load(_catalog[asset]) as PackedScene).instantiate() as Node3D
	for animation in piece.find_children("*", "AnimationPlayer", true, false):
		animation.active = false
	return piece


func _tar_meshes() -> Array[MeshInstance3D]:
	var result: Array[MeshInstance3D] = []
	for piece in _pieces.values():
		for mesh in piece.find_children("*", "MeshInstance3D", true, false):
			if String(mesh.name).ends_with("_darkness") and mesh.get_meta("player_equipped_surface", true):
				result.append(mesh)
	return result


func _refresh_hair_visibility() -> void:
	if not _pieces.has("body"):
		return
	var hidden: int = HEAD_HAIR_COVERAGE.get(head, 0) if _pieces.has("head") else 0
	if head_hair_mode > 0:
		hidden = [0, 0, 1, 2, 3][head_hair_mode]
	for mesh in _pieces["body"].find_children("*", "MeshInstance3D", true, false):
		var mask := 1 if String(mesh.name).begins_with("armor_hair_palm") else (2 if String(mesh.name).begins_with("armor_hair_fringe") else 0)
		if mask == 0:
			continue
		var shown := (mask & hidden) == 0
		mesh.set_meta("player_hair_visible", shown)
		mesh.visible = shown and (not String(mesh.name).ends_with("_darkness") or (not cinematic_render and (darkness_show_mask or use_original_tar_hp or health_ratio < 1.0)))
	_refresh_darkness()


func _prepare_tar_materials(piece: Node3D) -> void:
	for mesh in piece.find_children("*", "MeshInstance3D", true, false):
		if not String(mesh.name).ends_with("_darkness"):
			continue
		for index in range(mesh.mesh.get_surface_count()):
			var material = mesh.get_active_material(index)
			if material is ShaderMaterial:
				mesh.set_surface_override_material(index, material.duplicate())


func _load_tar_globals() -> void:
	var directory := characters_directory.get_base_dir().path_join("tar_globals")
	if darkness_tar_normal == null:
		var path := directory.path_join("waterwave_n2.png")
		if ResourceLoader.exists(path):
			darkness_tar_normal = load(path) as Texture2D
	if darkness_noise == null:
		var path := directory.path_join("AmbientSphere3.png")
		if ResourceLoader.exists(path):
			darkness_noise = load(path) as Texture2D


func _refresh_tar_textures() -> void:
	if not is_node_ready():
		return
	for mesh in _tar_meshes():
		for index in range(mesh.mesh.get_surface_count()):
			var material = mesh.get_active_material(index)
			if material is ShaderMaterial:
				material.set_shader_parameter("use_scene_lights", darkness_scene_lighting)
				material.set_shader_parameter("tar_normal_texture", darkness_tar_normal)
				material.set_shader_parameter("use_tar_normal_texture", darkness_tar_normal != null)
				material.set_shader_parameter("tar_noise_texture", darkness_noise)
				material.set_shader_parameter("use_tar_noise_texture", darkness_noise != null)
				material.set_shader_parameter("mat_param2", Vector4(0.0, 0.0, darkness_noise_strength, 0.0))
				for parameter in _tar_lighting:
					material.set_shader_parameter(parameter, _tar_lighting[parameter])


func set_tar_lighting(frame: Dictionary) -> void:
	# A scene adapter can provide the original frame uniforms without changing
	# the shader equations or sharing mutable materials with another player.
	if frame == _tar_lighting:
		return
	_tar_lighting = frame.duplicate()
	_refresh_tar_textures()


func _process(delta: float) -> void:
	if darkness_animate and not Engine.is_editor_hint():
		var direction := Vector2.from_angle(deg_to_rad(darkness_flow_angle))
		_tar_phase += direction * delta * darkness_flow_speed
		_tar_phase = Vector2(fposmod(_tar_phase.x, 1.0), fposmod(_tar_phase.y, 1.0))
	_update_tar_frame()


func _update_tar_frame() -> void:
	if not is_inside_tree() or not is_instance_valid(_skeleton) or not _skeleton.is_inside_tree():
		return
	var root_world := _skeleton.to_global(_skeleton.get_bone_global_pose(0).origin)
	for mesh in _tar_meshes():
		mesh.set_instance_shader_parameter("tar_root_world", root_world)
		mesh.set_instance_shader_parameter("tar_uv_offset", Vector4(0.0, 0.0, _tar_phase.x, _tar_phase.y))


func _refresh_darkness() -> void:
	if not is_node_ready():
		return
	# The extra feather puts both endpoints outside/at the mask range:
	# full health hides the overlay, zero health makes every mask value opaque.
	var width := maxf(darkness_mask_feather, 0.001)
	var cutoff := (1.0 - health_ratio) * (1.0 + width)
	var parameters := tar_hp if use_original_tar_hp else Vector3(cutoff, cutoff - width, 1.0 / width)
	for mesh in _tar_meshes():
		mesh.visible = mesh.get_meta("player_hair_visible", true) and not cinematic_render and (darkness_show_mask or use_original_tar_hp or health_ratio < 1.0)
		mesh.set_instance_shader_parameter("tar_depth_bias", darkness_depth_bias)
		mesh.set_instance_shader_parameter("tar_hp", parameters)
		mesh.set_instance_shader_parameter("tar_color", darkness_color)
		mesh.set_instance_shader_parameter("tar_reflection_strength", darkness_reflection)
		mesh.set_instance_shader_parameter("tar_normal_strength", darkness_normal_strength)
		mesh.set_instance_shader_parameter("tar_show_mask", darkness_show_mask)


func _sync_poses() -> void:
	if not is_instance_valid(_skeleton) or not is_inside_tree():
		return
	for key in _pieces:
		var piece = _pieces[key]
		if not is_instance_valid(piece):
			continue
		if key != "body":
			# Keep accessory axes aligned with the model at rest, then follow the
			# attachment bone's deformation rather than copying its bind axes.
			var bone_name := "bone_110" if key == "head" else ("bone_013" if key == "bracelets_positive" else "bone_043")
			var bone := _skeleton.find_bone(bone_name)
			if bone >= 0:
				var rest := _skeleton.get_bone_global_rest(bone)
				piece.transform = _skeleton.transform * _skeleton.get_bone_global_pose(bone) * rest.affine_inverse() * Transform3D(Basis.IDENTITY, rest.origin)
			continue
		var target := _find_skeleton(piece)
		if target == null:
			continue
		# Transfer deformation in skeleton space, not absolute local transforms.
		# Clean/contaminated rigs have different bind axes (jaw bone_120 is
		# rotated by 180 degrees) and some additional intermediary bones.
		var pending: Array[int] = []
		for root_bone in target.get_parentless_bones():
			pending.append(root_bone)
		var globals: Dictionary = {}
		while not pending.is_empty():
			var index: int = pending.pop_front()
			var parent := target.get_bone_parent(index)
			var parent_pose: Transform3D = globals.get(parent, Transform3D.IDENTITY)
			var source := _skeleton.find_bone(target.get_bone_name(index))
			var desired: Transform3D
			if source >= 0:
				desired = (_skeleton.get_bone_global_pose(source)
					* _skeleton.get_bone_global_rest(source).affine_inverse()
					* target.get_bone_global_rest(index))
			else:
				desired = parent_pose * target.get_bone_rest(index)
			globals[index] = desired
			target.set_bone_pose(index, parent_pose.affine_inverse() * desired)
			for child in target.get_bone_children(index):
				pending.append(child)
	_update_tar_frame()
