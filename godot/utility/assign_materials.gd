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
## a TwoBoneIK3D modifier. chr1 body manifests also describe the supported
## cloth/hair chains, imported as a SpringBoneSimulator3D after animation/IK.

const IK_CHAINS := [
	[&"bone_011", &"bone_013", &"bone_015", &"ik_hand_l_target", Vector3(0, -50, 25)],
	[&"bone_041", &"bone_043", &"bone_045", &"ik_hand_r_target", Vector3(0, -50, 25)],
	[&"bone_200", &"bone_201", &"bone_202", &"ik_foot_l_target", Vector3(0, 0, 50)],
	[&"bone_210", &"bone_211", &"bone_212", &"ik_foot_r_target", Vector3(0, 0, 50)],
]
const INSTANCE_MESHES_PATH := "res://terrain/foliage/meshes"
const BINDINGS_RELATIVE_PATH := "materials/material_bindings.json"

var _asset_kind := "model"
var _ik_target_orientation := "none"
var _model_scale := 1.0
var _cloth_surfaces: Array = []
var _spring_bone_chains: Array = []
var _spring_bone_collisions: Array = []

func _post_import(scene: Node) -> Object:
	var assignments := _load_bindings()
	if not assignments.is_empty():
		var missing := {}
		var applied := _assign_recursive(scene, assignments, missing)
		print("majin_import: assigned %d surface(s) for %s." % [applied, scene.name])
		if not missing.is_empty():
			push_warning("majin_import: unmatched material names: %s" % str(missing.keys()))
	_setup_humanoid_ik(scene)
	_setup_spring_bones(scene)
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

func _setup_spring_bones(scene: Node) -> void:
	if _spring_bone_chains.is_empty():
		return
	var skeleton := _find_skeleton(scene)
	if skeleton == null or skeleton.has_node("MajinSecondaryMotion") or skeleton.has_node("MajinClothConstraints"):
		return
	if not ClassDB.class_exists(&"SpringBoneSimulator3D"):
		push_warning("majin_import: secondary motion requires SpringBoneSimulator3D (Godot 4.4+)")
		return
	# A modifier runs after animation and the IK modifiers added above. Keep
	# the node owned by the imported scene so it survives packing/reimport.
	var spring := ClassDB.instantiate(&"SpringBoneSimulator3D") as Node
	spring.name = "MajinSecondaryMotion"
	skeleton.add_child(spring)
	spring.owner = scene
	spring.set("external_force", Vector3.ZERO)
	var count := 0
	var configured_chains: Array = []
	var cloth_bones: Array = []
	for surface in _cloth_surfaces:
		for column in surface.bone_columns:
			cloth_bones.append_array(column)
	for chain in _spring_bone_chains:
		if chain["root_bone"] in cloth_bones:
			continue # CLOTH grids have their own coupled solver.
		var root := skeleton.find_bone(chain["root_bone"])
		var end := skeleton.find_bone(chain["end_bone"])
		if root < 0 or end < 0:
			continue
		var ancestor := end
		var bone_indices: Array[int] = []
		while ancestor >= 0:
			bone_indices.push_front(ancestor)
			if ancestor == root:
				break
			ancestor = skeleton.get_bone_parent(ancestor)
		if ancestor != root:
			continue
		spring.set("setting_count", count + 1)
		spring.call("set_root_bone_name", count, chain["root_bone"])
		spring.call("set_end_bone_name", count, chain["end_bone"])
		# Persist resolved indices too: resolving an initially invalid end
		# on enter_tree would clear already serialized individual settings.
		spring.call("set_root_bone", count, root)
		spring.call("set_end_bone", count, end)
		spring.call("set_extend_end_bone", count, true)
		spring.call("set_end_bone_direction", count, 0) # local +X in DDM
		spring.call("set_end_bone_length", count, float(chain["end_length"]))
		spring.call("set_stiffness", count, float(chain["stiffness"]))
		spring.call("set_drag", count, float(chain["drag"]))
		# Upgrade old manifests which supplied (0,-1,0) globally, while
		# respecting an explicit zero in new per-joint profiles.
		var gravity := float(chain.get("gravity", 0.0))
		if chain.get("gravity_mode", "external_force") != "per_joint":
			gravity += 1.0
		spring.call("set_gravity", count, gravity)
		spring.call("set_gravity_direction", count, Vector3.DOWN)
		var radius := float(chain.get("radius", spring.call("get_radius", count)))
		spring.call("set_individual_config", count, true)
		# Imports are built outside SceneTree: native joint discovery has not
		# run yet. Allocate serialized joint slots from the verified hierarchy.
		spring.set("settings/%d/joint_count" % count, bone_indices.size())
		var joint_values := {}
		for joint in chain.get("joint_settings", []):
			joint_values[joint["bone"]] = joint
		for joint_index in bone_indices.size():
			var bone_name := skeleton.get_bone_name(bone_indices[joint_index])
			var joint: Dictionary = joint_values.get(bone_name, {})
			spring.call("set_joint_stiffness", count, joint_index, float(joint.get("stiffness", chain["stiffness"])))
			spring.call("set_joint_drag", count, joint_index, float(joint.get("drag", chain["drag"])))
			spring.call("set_joint_radius", count, joint_index, float(joint.get("radius", radius)))
			spring.call("set_joint_gravity", count, joint_index, float(joint.get("gravity", gravity)))
			var direction: Array = joint.get("gravity_direction", [0.0, -1.0, 0.0])
			spring.call("set_joint_gravity_direction", count, joint_index, Vector3(direction[0], direction[1], direction[2]))
		configured_chains.append(chain)
		count += 1
	if count == 0:
		skeleton.remove_child(spring)
		spring.free()
	else:
		_setup_spring_collisions(scene, skeleton, spring, configured_chains)
		print("majin_import: connected %d secondary-motion chains for %s." % [count, scene.name])
	if not _cloth_surfaces.is_empty():
		var script := load("res://majin_utility/cloth_constraints.gd") as Script
		if script == null or not script.can_instantiate():
			push_error("majin_import: install majin_utility/cloth_constraints.gd")
			return
		var cloth := SkeletonModifier3D.new()
		cloth.set_script(script)
		cloth.name = "MajinClothConstraints"
		cloth.set("gravity_acceleration", Vector3(0, -9.8, 0))
		cloth.set("external_force", Vector3.ZERO)
		cloth.set("surfaces", _cloth_surfaces)
		cloth.set("collisions", _spring_bone_collisions)
		skeleton.add_child(cloth)
		cloth.owner = scene

func _setup_spring_collisions(scene: Node, skeleton: Skeleton3D, spring: Node, chains: Array) -> void:
	if _spring_bone_collisions.is_empty():
		return
	var capsule_count := 0
	for entry in _spring_bone_collisions:
		var kind := "SpringBoneCollisionSphere3D" if entry["shape"] == "sphere" else "SpringBoneCollisionCapsule3D"
		if not ClassDB.class_exists(kind):
			push_warning("majin_import: missing collision class " + kind)
			continue
		var bone_a := skeleton.find_bone(entry["bone_a"])
		if bone_a < 0 or (entry["shape"] == "capsule" and skeleton.find_bone(entry["bone_b"]) < 0):
			push_warning("majin_import: missing CSR attachment bone for " + entry["name"])
			continue
		var collider := ClassDB.instantiate(kind) as Node3D
		collider.name = entry["name"]
		spring.add_child(collider)
		collider.owner = scene
		collider.set("radius", float(entry["radius"]))
		collider.set_meta("majin_csr_source", entry["source_record"])
		if entry["shape"] == "sphere":
			var point: Array = entry["point_a"]
			collider.set("bone_name", entry["bone_a"])
			collider.set("position_offset", Vector3(point[0], point[1], point[2]))
		else:
			collider.set_meta("majin_csr_endpoints", entry)
			capsule_count += 1
	# CSR contains several collision domains. SSR chooses a mask per simulated
	# group: a collider for the cape must not also repel hair or hip cloth.
	for index in range(chains.size()):
		var paths: Array[NodePath] = []
		for name in chains[index].get("collision_names", []):
			var collider := spring.get_node_or_null(NodePath(name))
			if collider != null:
				paths.append(spring.get_path_to(collider))
		spring.call("set_enable_all_child_collisions", index, false)
		spring.call("set_collision_count", index, paths.size())
		for collision_index in range(paths.size()):
			spring.call("set_collision_path", index, collision_index, paths[collision_index])
	if capsule_count > 0:
		var script := load("res://majin_utility/spring_collision_update.gd") as Script
		if script == null or not script.can_instantiate():
			push_error("majin_import: install majin_utility/spring_collision_update.gd for CSR capsules")
			return
		var updater := SkeletonModifier3D.new()
		updater.set_script(script)
		updater.name = "MajinSpringCollisionUpdate"
		skeleton.add_child(updater)
		updater.owner = scene
		skeleton.move_child(updater, spring.get_index())
		updater.call("update_collisions", skeleton, spring)
	print("majin_import: connected %d original CSR collision volumes." % spring.get_child_count())

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
	var poles: Array[Node] = []
	for chain in IK_CHAINS:
		var target := ClassDB.instantiate(&"ModifierBoneTarget3D") as Node
		target.name = "%s_node" % chain[3]
		target.set("bone_name", chain[3])
		skeleton.add_child(target)
		target.owner = scene
		targets.append(target)

		var pole_bone := StringName(String(chain[3]).replace("_target", "_pole"))
		var pole: Node
		if skeleton.find_bone(pole_bone) >= 0:
			pole = ClassDB.instantiate(&"ModifierBoneTarget3D") as Node
			pole.set("bone_name", pole_bone)
		else:
			var fallback := Node3D.new()
			var middle := skeleton.find_bone(chain[1])
			# IK_CHAINS offsets use the source DDM centimetre-like units. The
			# imported skeleton and animation translations have already been
			# multiplied by the GLB export scale (normally 0.01), so apply the
			# same factor to the synthetic pole offset.
			fallback.position = (
				skeleton.get_bone_global_rest(middle).origin
				+ chain[4] * _model_scale
			)
			pole = fallback
		pole.name = "%s_node" % pole_bone
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

func _load_bindings() -> Dictionary:
	# Load only this GLB's manifest. Searching child folders recursively can
	# accidentally pick up an instance manifest and extract an ordinary model.
	# Resource paths are relative to the model directory, not to ``materials/``.
	_asset_kind = "model"
	_model_scale = 1.0
	_ik_target_orientation = "none"
	_cloth_surfaces = []
	_spring_bone_chains = []
	_spring_bone_collisions = []
	var result := {}
	var model_base := get_source_file().get_base_dir()
	var manifest_path := model_base.path_join(BINDINGS_RELATIVE_PATH)
	if not FileAccess.file_exists(manifest_path):
		push_warning("majin_import: no manifest found at %s" % manifest_path)
		return result
	var data = JSON.parse_string(FileAccess.get_file_as_string(manifest_path))
	if typeof(data) != TYPE_DICTIONARY:
		push_warning("majin_import: invalid manifest at %s" % manifest_path)
		return result
	_asset_kind = data.get("asset_kind", "model")
	_model_scale = float(data.get("model_scale", 1.0))
	if not is_finite(_model_scale) or _model_scale <= 0.0:
		push_warning("majin_import: invalid model_scale; using 1.0")
		_model_scale = 1.0
	_ik_target_orientation = data.get("ik_target_orientation", "none")
	_cloth_surfaces = data.get("cloth_surfaces", [])
	_spring_bone_chains = data.get("spring_bone_chains", [])
	_spring_bone_collisions = data.get("spring_bone_collisions", [])
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
