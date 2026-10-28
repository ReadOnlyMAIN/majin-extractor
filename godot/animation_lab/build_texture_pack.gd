extends RefCounted
## Offline pack builder. Call generate() on a worker thread, then scan the editor.
const BASE := "res://original_files/decoded/chara/chr100/"
var color_sources: Dictionary = {}
var results: Array = []

func generate() -> Array:
	var error := DirAccess.make_dir_recursive_absolute(BASE + "textures_upscaled")
	if error != OK:
		return [{"error": error}]
	for file in DirAccess.get_files_at(BASE + "textures"):
		if file.get_extension() != "png":
			continue
		var source := Image.load_from_file(BASE + "textures/" + file)
		var image := Image.load_from_file(color_sources[file]) if color_sources.has(file) else source.duplicate() as Image
		image.resize(source.get_width() * 4, source.get_height() * 4, Image.INTERPOLATE_LANCZOS)
		if color_sources.has(file):
			source.resize(image.get_width(), image.get_height(), Image.INTERPOLATE_LANCZOS)
			source.convert(Image.FORMAT_RGBA8)
			image.convert(Image.FORMAT_RGBA8)
			var source_data := source.get_data()
			var data := image.get_data()
			for index in range(3, data.size(), 4):
				data[index] = source_data[index]
			image.set_data(image.get_width(), image.get_height(), false, Image.FORMAT_RGBA8, data)
		var saved := image.save_png(BASE + "textures_upscaled/" + file)
		results.append({"file": file, "width": image.get_width(), "height": image.get_height(), "error": saved})
	return results


var normal_sources: Dictionary = {}


func generate_normals() -> Array:
	var output: Array = []
	var backup := BASE + "textures_upscaled/.normals_before_ai/"
	var error := DirAccess.make_dir_recursive_absolute(backup)
	if error != OK:
		return [{"error": error}]
	for file in normal_sources:
		var destination: String = BASE + "textures_upscaled/" + file
		if not FileAccess.file_exists(backup + file):
			error = DirAccess.copy_absolute(destination, backup + file)
			if error != OK:
				return [{"file": file, "error": error}]
		var source := Image.load_from_file(BASE + "textures/" + file)
		var image := Image.load_from_file(normal_sources[file])
		if source == null or image == null:
			return [{"file": file, "error": ERR_FILE_CANT_READ}]
		image.resize(source.get_width() * 4, source.get_height() * 4, Image.INTERPOLATE_LANCZOS)
		source.resize(image.get_width(), image.get_height(), Image.INTERPOLATE_LANCZOS)
		image.convert(Image.FORMAT_RGBA8)
		source.convert(Image.FORMAT_RGBA8)
		var data := image.get_data()
		var source_data := source.get_data()
		for index in range(0, data.size(), 4):
			# Normal vectors are linear data; retain the source Y convention.
			var normal := Vector3(data[index], data[index + 1], data[index + 2]) / 127.5 - Vector3.ONE
			normal = normal.normalized() if normal.length_squared() > 0.000001 else Vector3(0, 0, 1)
			data[index] = clampi(roundi((normal.x + 1.0) * 127.5), 0, 255)
			data[index + 1] = clampi(roundi((normal.y + 1.0) * 127.5), 0, 255)
			data[index + 2] = clampi(roundi((normal.z + 1.0) * 127.5), 0, 255)
			data[index + 3] = source_data[index + 3]
		image.set_data(image.get_width(), image.get_height(), false, Image.FORMAT_RGBA8, data)
		error = image.save_png(destination)
		output.append({"file": file, "width": image.get_width(), "height": image.get_height(), "error": error})
	return output
