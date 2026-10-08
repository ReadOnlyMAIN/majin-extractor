@tool
extends EditorScenePostImport

## Universal post-import for converted Majin and the Forsaken Kingdom GLBs.
##
## Use this as the Godot 4 "Import Script" of the converted .glb (see
## godot/README.md). Godot calls _post_import() after every import or reimport,
## so the materials are applied automatically and survive asset reimports.
##
## A glTF/GLB import cannot reference external Godot resources, so Godot creates
## a StandardMaterial3D for every surface. This script walks the imported scene
## and, for each mesh surface, looks up the material name in the sibling
## ``material_bindings.json`` and assigns the matching Material resource.
##
## Each model manifest is discovered from the imported scene's own directory.
## Character files that contain the four exported IK controls additionally get
## a TwoBoneIK3D modifier. Other GLBs simply skip that step.

const IK_CHAINS := [
	[&"bone_011", &"bone_013", &"bone_015", &"ik_hand_l_target", Vector3(0, -50, 25)],
	[&"bone_041", &"bone_043", &"bone_045", &"ik_hand_r_target", Vector3(0, -50, 25)],
	[&"bone_200", &"bone_201", &"bone_202", &"ik_foot_l_target", Vector3(0, 0, 50)],
	[&"bone_210", &"bone_211", &"bone_212", &"ik_foot_r_target", Vector3(0, 0, 50)],
]
const INSTANCE_MESHES_PATH := "res://terrain/foliage/meshes"

var _asset_kind := "model"
var _ik_target_orientation := "none"

func _post_import(scene: Node) -> Object:
	var assignments := _load_bindings(scene)
	if not assignments.is_empty():
		var missing := {}
		var applied := _assign_recursive(scene, assignments, missing)
		print("majin_import: assigned %d surface(s) for %s." % [applied, scene.name])
		if not missing.is_empty():
			push_warning("majin_import: unmatched material names: %s" % str(missing.keys()))
	_setup_humanoid_ik(scene)
	if _asset_kind == "instance":
		_extract_instance_mesh(scene, assignments)
	return scene

func _find_skeleton(node: Node) -> Skeleton3D:
	if node is Skeleton3D:
		return node
	for child in node.get_children():
		var result := _find_skeleton(child)
		if result != null:
			return result
	return null

func _setup_humanoid_ik(scene: Node) -> bool:
	var skeleton := _find_skeleton(scene)
	if skeleton == null:
		return false
	var present_targets := 0
	for chain in IK_CHAINS:
		if skeleton.find_bone(chain[3]) >= 0:
			present_targets += 1
	if present_targets == 0:
		return false
	if present_targets != IK_CHAINS.size():
		push_warning("majin_import: incomplete humanoid IK target set for %s" % scene.name)
		return false
	if (not ClassDB.class_exists(&"TwoBoneIK3D")
			or not ClassDB.class_exists(&"ModifierBoneTarget3D")):
		push_warning("majin_import: this Godot version lacks the required 3D IK classes")
		return false

	var targets: Array[Node] = []
	var poles: Array[Node3D] = []
	for chain in IK_CHAINS:
		var target := ClassDB.instantiate(&"ModifierBoneTarget3D") as Node
		target.name = "%s_node" % chain[3]
		target.set("bone_name", chain[3])
		skeleton.add_child(target)
		target.owner = scene
		targets.append(target)

		var pole := Node3D.new()
		pole.name = "%s_pole" % chain[3]
		var middle := skeleton.find_bone(chain[1])
		pole.position = skeleton.get_bone_global_rest(middle).origin + chain[4]
		skeleton.add_child(pole)
		pole.owner = scene
		poles.append(pole)

	var ik := ClassDB.instantiate(&"TwoBoneIK3D") as Node
	ik.name = "MajinTwoBoneIK"
	skeleton.add_child(ik)
	ik.owner = scene
	ik.set("setting_count", IK_CHAINS.size())
	for index in range(IK_CHAINS.size()):
		var chain = IK_CHAINS[index]
		ik.call("set_root_bone_name", index, chain[0])
		ik.call("set_middle_bone_name", index, chain[1])
		ik.call("set_end_bone_name", index, chain[2])
		ik.call("set_target_node", index, ik.get_path_to(targets[index]))
		ik.call("set_pole_node", index, ik.get_path_to(poles[index]))

	# TwoBoneIK3D intentionally ignores the target basis. Apply the decoded
	# target orientation afterwards without replacing the IK-solved position.
	if (_ik_target_orientation == "source-row"
			and ClassDB.class_exists(&"CopyTransformModifier3D")):
		var orientation_copy := ClassDB.instantiate(
			&"CopyTransformModifier3D"
		) as Node
		orientation_copy.name = "MajinEndEffectorOrientation"
		skeleton.add_child(orientation_copy)
		orientation_copy.owner = scene
		orientation_copy.set("setting_count", IK_CHAINS.size())
		for index in range(IK_CHAINS.size()):
			var chain = IK_CHAINS[index]
			orientation_copy.call(
				"set_reference_bone_name", index, chain[3]
			)
			orientation_copy.call("set_apply_bone_name", index, chain[2])
			orientation_copy.call("set_copy_flags", index, 2) # rotation only
			orientation_copy.call("set_global", index, true)
			orientation_copy.call("set_relative", index, false)
			orientation_copy.call("set_additive", index, false)
	elif _ik_target_orientation == "source-row":
		push_warning(
			"majin_import: target rotations exported, but this Godot version " +
			"has no CopyTransformModifier3D"
		)
	print("majin_import: connected humanoid IK and target orientations for %s." % scene.name)
	return true

func _load_bindings(scene: Node) -> Dictionary:
	# ~{material_name: Material}: built from every manifest found next to
	# the imported scene. Manifest resource paths are relative to the model
	# directory, not to the ``materials/`` directory containing the manifest.
	var result := {}
	var model_base := get_source_file().get_base_dir()
	for path in _find_files(model_base, "material_bindings.json"):
		var text := FileAccess.get_file_as_string(path)
		var data = JSON.parse_string(text)
		if typeof(data) != TYPE_DICTIONARY:
			continue
		_asset_kind = data.get("asset_kind", _asset_kind)
		_ik_target_orientation = data.get(
			"ik_target_orientation", _ik_target_orientation
		)
		for entry in data.get("materials", []):
			var name: String = entry.get("material_name", "")
			# ``shader_material`` is the legacy manifest key used before native
			# StandardMaterial3D resources were emitted.
			var tres: String = entry.get("material_resource", entry.get("shader_material", ""))
			if name == "" or tres == "":
				continue
			var resource_path := tres if tres.is_absolute_path() else model_base.path_join(tres)
			var resource := load(resource_path)
			if resource is Material:
				result[name] = resource
	return result

func _extract_instance_mesh(scene: Node, assignments: Dictionary) -> bool:
	var mesh_instances: Array[MeshInstance3D] = []
	_collect_mesh_instances(scene, mesh_instances)
	if mesh_instances.size() != 1:
		push_warning(
			"majin_import: instance %s has %d meshes; skipping .res extraction" %
			[scene.name, mesh_instances.size()]
		)
		return false
	var source_mesh := mesh_instances[0].mesh
	if source_mesh == null:
		return false
	var extracted := source_mesh.duplicate(true) as Mesh
	var ignored_missing := {}
	_apply_materials_to_mesh(extracted, assignments, ignored_missing)
	var model_name := get_source_file().get_file().get_basename()
	extracted.resource_name = model_name
	var absolute_dir := ProjectSettings.globalize_path(INSTANCE_MESHES_PATH)
	var directory_error := DirAccess.make_dir_recursive_absolute(absolute_dir)
	if directory_error != OK:
		push_error("majin_import: cannot create %s" % INSTANCE_MESHES_PATH)
		return false
	var mesh_path := INSTANCE_MESHES_PATH.path_join(model_name + ".res")
	var save_error := ResourceSaver.save(extracted, mesh_path)
	if save_error != OK:
		push_error("majin_import: cannot save %s (error %d)" % [mesh_path, save_error])
		return false
	mesh_instances[0].mesh = ResourceLoader.load(
		mesh_path, "Mesh", ResourceLoader.CACHE_MODE_REPLACE
	) as Mesh
	print("majin_import: extracted reusable instance %s." % mesh_path)
	return true

func _collect_mesh_instances(node: Node, result: Array[MeshInstance3D]) -> void:
	if node is MeshInstance3D:
		result.append(node)
	for child in node.get_children():
		_collect_mesh_instances(child, result)

func _apply_materials_to_mesh(
	mesh: Mesh, assignments: Dictionary, missing: Dictionary
) -> int:
	var applied := 0
	for surface in range(mesh.get_surface_count()):
		var current := mesh.surface_get_material(surface)
		var material_name := ""
		if current != null:
			material_name = current.resource_name
		if assignments.has(material_name):
			mesh.surface_set_material(surface, assignments[material_name])
			applied += 1
		elif material_name != "":
			missing[material_name] = true
	return applied

func _assign_recursive(node: Node, assignments: Dictionary, missing: Dictionary) -> int:
	var count := 0
	if node is MeshInstance3D:
		var mesh: Mesh = node.mesh
		if mesh != null:
			for surface in mesh.get_surface_count():
				var current := mesh.surface_get_material(surface)
				var name := ""
				if current != null:
					name = current.resource_name
				if assignments.has(name):
					node.set_surface_override_material(surface, assignments[name])
					count += 1
				elif name != "":
					missing[name] = true
	for child in node.get_children():
		count += _assign_recursive(child, assignments, missing)
	return count

func _find_files(dir_path: String, file_name: String) -> PackedStringArray:
	var found := PackedStringArray()
	var dir := DirAccess.open(dir_path)
	if dir == null:
		return found
	dir.list_dir_begin()
	var entry := dir.get_next()
	while entry != "":
		if dir.current_is_dir() and not entry.begins_with("."):
			found.append_array(_find_files(dir_path.path_join(entry), file_name))
		elif entry == file_name:
			found.append(dir_path.path_join(entry))
		entry = dir.get_next()
	dir.list_dir_end()
	return found
