@tool
extends EditorScript

## Assign the generated ShaderMaterial .tres files to an imported DDM GLB.
##
## A glTF/GLB import cannot reference external Godot resources, so Godot creates
## a StandardMaterial3D for every surface. This script walks the currently open
## scene (or a chosen model directory) and, for each mesh surface, looks up the
## material name in the sibling ``material_bindings.json`` and assigns the
## matching ShaderMaterial.
##
## Usage in the Godot 4 editor:
##   1. Open this file and run it (File > Run), or attach it to a @tool script.
##   2. It defaults to the directory of the imported model. Adjust MODEL_DIR if
##      you run it from a different location.

const MODEL_DIR := "res://"

func _run() -> void:
	var assignments := _load_bindings()
	if assignments.is_empty():
		push_warning("assign_materials: no material_bindings.json found under %s" % MODEL_DIR)
		return
	var roots := []
	if Engine.is_editor_hint():
		roots = EditorInterface.get_selection().get_selected_nodes()
	if roots.is_empty():
		roots = _find_mesh_roots()
	var applied := 0
	var missing := {}
	for root in roots:
		applied += _assign_recursive(root, assignments, missing)
	print("assign_materials: assigned %d surface(s)." % applied)
	if not missing.is_empty():
		push_warning("assign_materials: unmatched material names: %s" % str(missing.keys()))

func _load_bindings() -> Dictionary:
	# ~{material_name: ShaderMaterial}: built from every manifest found under
	# MODEL_DIR so a whole export tree can be fixed in one run.
	var result := {}
	for path in _find_files(MODEL_DIR, "material_bindings.json"):
		var text := FileAccess.get_file_as_string(path)
		var data = JSON.parse_string(text)
		if typeof(data) != TYPE_DICTIONARY:
			continue
		var base := path.get_base_dir()
		for entry in data.get("materials", []):
			var name: String = entry.get("material_name", "")
			var tres: String = entry.get("shader_material", "")
			if name == "" or tres == "":
				continue
			var resource := load(base.path_join(tres))
			if resource is ShaderMaterial:
				result[name] = resource
	return result

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

func _find_mesh_roots() -> Array:
	var scene := EditorInterface.get_edited_scene_root()
	return [scene] if scene != null else []

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
