extends Node3D
## Animation Lab attachments with user-confirmed equipment compatibility.
## Unmapped chr3 rigs retain a restricted preview for identifying their equipment.

const HANDS := {"right": [41, 43, 45], "left": [11, 13, 15]}
const GRIP_BONES := {"right": "bone_250", "left": "bone_251"}
const COMPATIBILITY := {
	"chr1": {"right": ["chr900", "chr980", "chr990", "chr991", "chr992"], "left": ["chr905"]},
	"chr300": {"right": ["chr910"], "left": []},
	"chr301": {"right": ["chr910"], "left": ["chr920"]},
	"chr302": {"right": ["chr911"], "left": ["chr921"]},
	"chr303": {"right": ["chr912"], "left": ["chr922"]},
}
const CHR1_EQUIPMENT := ["chr900", "chr905", "chr980", "chr990", "chr991", "chr992"]
const LEFT_HAND_ACCESSORIES := ["chr920", "chr921", "chr922"]
var character: Node3D
var rig: Skeleton3D
var catalog: Dictionary = {}
var selected := {"right": "none", "left": "none"}
var instances: Dictionary = {}


func configure(target: Node3D, directory: String) -> void:
	clear()
	character = target
	for folder in DirAccess.get_directories_at(directory):
		if folder.length() != 6 or not folder.begins_with("chr9") or not folder.substr(3).is_valid_int():
			continue
		var path := directory.path_join(folder).path_join(folder + ".glb")
		if ResourceLoader.exists(path, "PackedScene"):
			catalog[folder] = path
	update_attachment()


func clear() -> void:
	if is_instance_valid(rig) and rig.skeleton_updated.is_connected(_sync):
		rig.skeleton_updated.disconnect(_sync)
	rig = null
	character = null
	for instance in instances.values():
		instance.free()
	instances.clear()
	catalog.clear()
	selected = {"right": "none", "left": "none"}


func character_id() -> String:
	if not is_instance_valid(character):
		return ""
	var id := character.scene_file_path.get_file().get_basename()
	return "chr1" if id == "chr1" or (id.length() == 6 and id.begins_with("chr1") and id.substr(3).is_valid_int()) else id


func compatibility_confirmed() -> bool:
	return COMPATIBILITY.has(character_id())


func supports(hand: String) -> bool:
	if not is_instance_valid(rig) or not rig.is_inside_tree():
		return false
	if compatibility_confirmed() and COMPATIBILITY[character_id()][hand].is_empty():
		return false
	var chain: Array = HANDS[hand]
	var parent := rig.find_bone("bone_%03d" % chain[0])
	if parent < 0:
		return false
	for id in chain.slice(1):
		var bone := rig.find_bone("bone_%03d" % id)
		if bone < 0 or rig.get_bone_parent(bone) != parent:
			return false
		parent = bone
	var grip := rig.find_bone(GRIP_BONES[hand])
	return grip >= 0 and rig.get_bone_parent(grip) == parent


func available(hand: String) -> Array:
	if not supports(hand):
		return []
	var result: Array = []
	if compatibility_confirmed():
		for asset in COMPATIBILITY[character_id()][hand]:
			if catalog.has(asset):
				result.append(asset)
	else:
		for asset in catalog:
			if asset not in CHR1_EQUIPMENT and (asset in LEFT_HAND_ACCESSORIES) == (hand == "left"):
				result.append(asset)
	result.sort()
	result.push_front("none")
	return result


func equip(hand: String, asset: String) -> void:
	if not available(hand).has(asset):
		return
	# Load before removing the current equipment, so a failed import is harmless.
	var replacement: Node3D
	if asset != "none":
		var packed := load(catalog[asset]) as PackedScene
		if packed == null:
			return
		replacement = packed.instantiate() as Node3D
		if replacement == null:
			return
	if instances.has(hand):
		instances[hand].free()
		instances.erase(hand)
	selected[hand] = asset
	if replacement != null:
		replacement.name = hand.capitalize() + "Equipment"
		add_child(replacement)
		instances[hand] = replacement
	_sync()


func _process(_delta: float) -> void:
	update_attachment()


func update_attachment() -> void:
	var target: Skeleton3D
	if is_instance_valid(character):
		var id := character.scene_file_path.get_file().get_basename()
		var numbered := id.length() == 6 and id.substr(3).is_valid_int()
		if id == "chr1" or (numbered and (id.begins_with("chr1") or id.begins_with("chr3"))):
			for mesh in character.find_children("*", "MeshInstance3D", true, false):
				if mesh.skin != null and mesh.is_visible_in_tree():
					target = mesh.get_node_or_null(mesh.skeleton) as Skeleton3D
					if target != null:
						break
	if rig != target:
		if is_instance_valid(rig) and rig.skeleton_updated.is_connected(_sync):
			rig.skeleton_updated.disconnect(_sync)
		rig = target
		if is_instance_valid(rig):
			rig.skeleton_updated.connect(_sync)
	_sync()


func _sync() -> void:
	if not is_inside_tree():
		return
	for hand in instances:
		var instance: Node3D = instances[hand]
		instance.visible = supports(hand)
		if instance.visible:
			var bone := rig.find_bone(GRIP_BONES[hand])
			instance.global_transform = rig.global_transform * rig.get_bone_global_pose(bone)
