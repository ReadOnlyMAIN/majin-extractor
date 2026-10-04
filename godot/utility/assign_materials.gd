@tool
extends EditorScenePostImport

## Assign the generated ShaderMaterial .tres files to an imported DDM GLB.
##
## Use this as the Godot 4 "Import Script" of the converted .glb (see
## godot/README.md). Godot calls _post_import() after every import or reimport,
## so the materials are applied automatically and survive asset reimports.
##
## A glTF/GLB import cannot reference external Godot resources, so Godot creates
## a StandardMaterial3D for every surface. This script walks the imported scene
## and, for each mesh surface, looks up the material name in the sibling
## ``material_bindings.json`` and assigns the matching ShaderMaterial.
##
## MATERIALS_DIR is where the sibling ``materials/`` folder was installed; each
## model manifest is discovered from the imported scene's own directory.

const MATERIALS_DIR := "res://majin_utility"

func _post_import(scene: Node) -> Object:
	var assignments := _load_bindings(scene)
	if assignments.is_empty():
		push_warning("assign_materials: no material_bindings.json found for %s" % scene.name)
		return scene
	var missing := {}
	var applied := _assign_recursive(scene, assignments, missing)
	print("assign_materials: assigned %d surface(s) for %s." % [applied, scene.name])
	if not missing.is_empty():
		push_warning("assign_materials: unmatched material names: %s" % str(missing.keys()))
	return scene

func _load_bindings(scene: Node) -> Dictionary:
	# ~{material_name: ShaderMaterial}: built from every manifest found next to
	# the imported scene (so sibling ``materials/*.tres`` resolve correctly).
	var result := {}
	for path in _find_files(get_source_file().get_base_dir(), "material_bindings.json"):
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
