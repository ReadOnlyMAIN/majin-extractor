extends RefCounted
## Instance-local texture replacement: imported/shared materials stay untouched.

const SOURCE := "res://original_files/decoded/chara/chr100/textures/"
const UPSCALED := "res://original_files/decoded/chara/chr100/textures_upscaled/"

var originals: Dictionary = {}
var textures: Dictionary = {}


func available() -> bool:
	return ResourceLoader.exists(UPSCALED + "chr100_c01.png")


func clear() -> void:
	originals.clear()
	textures.clear()


func apply(root: Node3D, enabled: bool) -> int:
	var replaced := 0
	for instance in root.find_children("*", "MeshInstance3D", true, false):
		var mesh := instance as MeshInstance3D
		if mesh.mesh == null:
			continue
		for surface in range(mesh.mesh.get_surface_count()):
			var material := mesh.get_active_material(surface) as ShaderMaterial
			if material == null or material.shader == null:
				continue
			if not originals.has(material):
				var slots: Dictionary = {}
				for uniform in material.shader.get_shader_uniform_list():
					var value: Variant = material.get_shader_parameter(uniform.name)
					if value is Texture2D and value.resource_path.begins_with(SOURCE):
						var path: String = UPSCALED + value.resource_path.get_file()
						if ResourceLoader.exists(path):
							slots[uniform.name] = value
				if slots.is_empty():
					continue
				material = material.duplicate() as ShaderMaterial
				if mesh.material_override != null:
					mesh.material_override = material
				else:
					mesh.set_surface_override_material(surface, material)
				originals[material] = slots
			for parameter in originals[material]:
				var original: Texture2D = originals[material][parameter]
				var path := UPSCALED + original.resource_path.get_file()
				if enabled and not textures.has(path):
					textures[path] = load(path) as Texture2D
				material.set_shader_parameter(parameter, textures[path] if enabled else original)
				replaced += 1
	return replaced
