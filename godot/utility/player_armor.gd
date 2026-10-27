@tool
extends Node3D
## Three equipment slots reconstructed from skin influences, not game unlock data.
## Each piece keeps its own Skin and bind pose; common bones follow chr100 by name.

@export_dir var characters_directory := "res://original_files/decoded/chara"
@export var head := "chr101":
	set(value):
		head = value
		if is_node_ready():
			_equip("head", value)
@export var torso := "chr101":
	set(value):
		torso = value
		if is_node_ready():
			_equip("torso", value)
@export var legs := "chr101":
	set(value):
		legs = value
		if is_node_ready():
			_equip("legs", value)

# Pairs identified visually by the user. Later packages remain unclassified.
const ARMOR_PAIRS := {
	"chr101": "chr100", "chr111": "chr110", "chr116": "chr115",
	"chr121": "chr120", "chr126": "chr125", "chr131": "chr130",
	"chr136": "chr135", "chr141": "chr140",
}

## Full health = clean; zero health = full height. The mapping is a preview
## approximation; the game's exact health curve and feather are not decoded.
@export_range(0.0, 1.0, 0.01) var health_ratio := 1.0:
	set(value):
		health_ratio = clampf(value, 0.0, 1.0)
		_refresh_darkness()
@export var darkness_height_range := Vector2(-0.1, 1.8):
	set(value):
		darkness_height_range = value
		_refresh_darkness()
@export_range(0.001, 0.2, 0.001) var darkness_feather := 0.04:
	set(value):
		darkness_feather = value
		_refresh_darkness()


var _base: Node3D
var _skeleton: Skeleton3D
var _pieces: Dictionary = {}
var _catalog: Dictionary = {}


func _ready() -> void:
	for folder in DirAccess.get_directories_at(characters_directory):
		if folder.length() == 6 and folder.begins_with("chr1") and folder.substr(3).is_valid_int():
			var path := characters_directory.path_join(folder).path_join(folder + ".glb")
			if ResourceLoader.exists(path):
				_catalog[folder] = path
	notify_property_list_changed()
	if not _catalog.has("chr100"):
		push_warning("Player armor requires an exported chr100 in " + characters_directory)
		return
	_base = (load(_catalog["chr100"]) as PackedScene).instantiate()
	add_child(_base)
	_skeleton = _find_skeleton(_base)
	for mesh in _base.find_children("*", "MeshInstance3D", true, false):
		mesh.hide()
	if _skeleton == null:
		push_error("chr100 has no Skeleton3D")
		return
	_skeleton.skeleton_updated.connect(_sync_poses)
	_equip("head", head)
	_equip("torso", torso)
	_equip("legs", legs)
	_refresh_darkness.call_deferred()


func _validate_property(property: Dictionary) -> void:
	if property.name in ["head", "torso", "legs"]:
		var names := available_armor()
		property.hint = PROPERTY_HINT_ENUM
		property.hint_string = ",".join(names)


func available_armor() -> Array:
	var names: Array[String] = []
	for asset in ARMOR_PAIRS:
		if _catalog.has(asset):
			names.append(asset)
	names.sort()
	return names


func get_animation_player() -> AnimationPlayer:
	if _base == null:
		return null
	return _base.find_child("AnimationPlayer", true, false) as AnimationPlayer


func _find_skeleton(root: Node) -> Skeleton3D:
	if root is Skeleton3D:
		return root as Skeleton3D
	for child in root.get_children():
		var found := _find_skeleton(child)
		if found != null:
			return found
	return null


func _equip(slot: String, asset: String) -> void:
	if not is_instance_valid(_skeleton):
		return
	# Accept older saved scenes that selected the contaminated package directly.
	for clean in ARMOR_PAIRS:
		if ARMOR_PAIRS[clean] == asset:
			asset = clean
	if not ARMOR_PAIRS.has(asset) or not _catalog.has(asset):
		push_warning("Unclassified or missing armor: " + asset)
		return
	_install_piece(slot, asset, "armor_" + slot)
	var overlay_key := slot + "_darkness"
	var overlay_asset: String = ARMOR_PAIRS[asset]
	if _pieces.has(overlay_key):
		var previous: Node = _pieces[overlay_key]
		_pieces.erase(overlay_key)
		if is_instance_valid(previous):
			remove_child(previous)
			previous.queue_free()
	if _catalog.has(overlay_asset):
		_install_piece(overlay_key, overlay_asset, "armor_" + overlay_key)
	_refresh_darkness()
	_sync_poses()


func _install_piece(key: String, asset: String, mesh_name: String) -> void:
	var piece := (load(_catalog[asset]) as PackedScene).instantiate() as Node3D
	var matching := false
	for mesh in piece.find_children("*", "MeshInstance3D", true, false):
		mesh.visible = String(mesh.name) == mesh_name
		matching = matching or mesh.visible
	if not matching:
		piece.free()
		push_warning(asset + " has no " + mesh_name + "; re-export or inspect its materials")
		return
	for animation in piece.find_children("*", "AnimationPlayer", true, false):
		animation.active = false
	if _pieces.has(key) and is_instance_valid(_pieces[key]):
		remove_child(_pieces[key])
		_pieces[key].queue_free()
	_pieces[key] = piece
	add_child(piece)


func _refresh_darkness() -> void:
	if not is_node_ready():
		return
	var height := lerpf(darkness_height_range.x, darkness_height_range.y, 1.0 - health_ratio)
	for key in _pieces:
		if not String(key).ends_with("_darkness"):
			continue
		var piece: Node3D = _pieces[key]
		if not is_instance_valid(piece):
			continue
		piece.visible = health_ratio < 1.0
		for mesh in piece.find_children("*", "MeshInstance3D", true, false):
			mesh.set_instance_shader_parameter("darkness_height", height)
			mesh.set_instance_shader_parameter("darkness_feather", darkness_feather)


func _sync_poses() -> void:
	if not is_instance_valid(_skeleton) or not is_inside_tree():
		return
	var root_y := (_skeleton.get_bone_global_pose(0).origin.y
		- _skeleton.get_bone_global_rest(0).origin.y)
	for key in _pieces:
		var piece = _pieces[key]
		if not is_instance_valid(piece):
			continue
		if String(key).ends_with("_darkness"):
			for mesh in piece.find_children("*", "MeshInstance3D", true, false):
				mesh.set_instance_shader_parameter("darkness_root_y", root_y)
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
