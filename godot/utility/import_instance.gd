@tool
extends EditorScenePostImport

## Import one complete DDM instance GLB as a reusable Godot Mesh resource.
##
## The DDM converter automatically exports assets below KB/instance as one GLB
## mesh. Use this script as their Godot 4 Import Script. It resolves the native
## or custom materials from material_bindings.json, writes them directly onto
## the mesh surfaces, then saves:
##
##     res://terrain/foliage/meshes/<source GLB name>.res
##
## Applying materials to the mesh itself (rather than as node overrides) makes
## them survive extraction into the standalone .res resource.

const MESHES_PATH := "res://terrain/foliage/meshes"
const BINDINGS_FILE := "material_bindings.json"


func _post_import(scene: Node) -> Object:
	var mesh_instances: Array[MeshInstance3D] = []
	_collect_mesh_instances(scene, mesh_instances)
	if mesh_instances.size() != 1:
		push_error((
			"import_instance: expected exactly one MeshInstance3D in %s, found %d. " +
			"Export KB/instance models with object mode 'auto' or 'single'."
		) % [scene.name, mesh_instances.size()])
		return scene

	var source_mesh := mesh_instances[0].mesh
	if source_mesh == null:
		push_error("import_instance: %s has no mesh." % scene.name)
		return scene

	var assignments := _load_bindings()
	if assignments.is_empty():
		push_warning("import_instance: no usable %s found for %s." % [BINDINGS_FILE, scene.name])

	# Never mutate the importer-owned mesh in place. The deep duplicate becomes
	# the standalone resource and receives persistent per-surface materials.
	var extracted := source_mesh.duplicate(true) as Mesh
	var missing := {}
	var applied := _apply_materials(extracted, assignments, missing)
	var model_name := get_source_file().get_file().get_basename()
	extracted.resource_name = model_name

	var absolute_mesh_dir := ProjectSettings.globalize_path(MESHES_PATH)
	var directory_error := DirAccess.make_dir_recursive_absolute(absolute_mesh_dir)
	if directory_error != OK:
		push_error("import_instance: cannot create %s (error %d)." % [MESHES_PATH, directory_error])
		return scene

	var mesh_path := MESHES_PATH.path_join(model_name + ".res")
	var save_error := ResourceSaver.save(extracted, mesh_path)
	if save_error != OK:
		push_error("import_instance: cannot save %s (error %d)." % [mesh_path, save_error])
		return scene

	# Keep the imported scene consistent with the resource consumed by foliage.
	mesh_instances[0].mesh = ResourceLoader.load(
		mesh_path, "Mesh", ResourceLoader.CACHE_MODE_REPLACE
	) as Mesh
	print(
		"import_instance: saved %s with %d assigned surface material(s)." %
		[mesh_path, applied]
	)
	if not missing.is_empty():
		push_warning("import_instance: unmatched material names: %s" % str(missing.keys()))
	return scene


func _collect_mesh_instances(node: Node, result: Array[MeshInstance3D]) -> void:
	if node is MeshInstance3D:
		result.append(node)
	for child in node.get_children():
		_collect_mesh_instances(child, result)


func _load_bindings() -> Dictionary:
	var result := {}
	var model_base := get_source_file().get_base_dir()
	for path in _find_files(model_base, BINDINGS_FILE):
		var data = JSON.parse_string(FileAccess.get_file_as_string(path))
		if typeof(data) != TYPE_DICTIONARY:
			continue
		for entry in data.get("materials", []):
			var material_name: String = entry.get("material_name", "")
			var material_path: String = entry.get(
				"material_resource", entry.get("shader_material", "")
			)
			if material_name == "" or material_path == "":
				continue
			var resolved_path := material_path if material_path.is_absolute_path() else model_base.path_join(material_path)
			var material := ResourceLoader.load(resolved_path)
			if material is Material:
				result[material_name] = material
	return result


func _apply_materials(mesh: Mesh, assignments: Dictionary, missing: Dictionary) -> int:
	var applied := 0
	for surface in mesh.get_surface_count():
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


func _find_files(dir_path: String, file_name: String) -> PackedStringArray:
	var found := PackedStringArray()
	var directory := DirAccess.open(dir_path)
	if directory == null:
		return found
	directory.list_dir_begin()
	var entry := directory.get_next()
	while entry != "":
		if directory.current_is_dir() and not entry.begins_with("."):
			found.append_array(_find_files(dir_path.path_join(entry), file_name))
		elif entry == file_name:
			found.append(dir_path.path_join(entry))
		entry = directory.get_next()
	directory.list_dir_end()
	return found
