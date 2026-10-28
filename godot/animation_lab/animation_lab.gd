extends Node3D
## Node-authored animation viewer. Uses Godot's normal imported PackedScenes.

@export_dir var characters_directory := "res://original_files/decoded/chara"
@export var initial_character := "chr1"

@onready var model_root: Node3D = $ModelRoot
@onready var camera: Camera3D = $Camera
@onready var ground: MeshInstance3D = $Ground
@onready var overlay: MeshInstance3D = $SkeletonOverlay
@onready var controls: VBoxContainer = $UI/Panel/Scroll/Margin/Controls
@onready var characters: OptionButton = controls.get_node("Character")
@onready var animation_list: ItemList = controls.get_node("Animations")
@onready var filter: LineEdit = controls.get_node("Filter")
@onready var timeline: HSlider = controls.get_node("Timeline")
@onready var clock: Label = controls.get_node("Time")
@onready var info: Label = controls.get_node("Info")
@onready var play_button: Button = controls.get_node("Playback/Play")
@onready var speed: SpinBox = controls.get_node("Rate/Speed")
@onready var loop: CheckButton = controls.get_node("Options/Loop")
@onready var bones: CheckButton = controls.get_node("Options/Bones")
@onready var follow: CheckButton = controls.get_node("Options/Follow")

@onready var customization: PanelContainer = $UI/Customization
@onready var equipment: VBoxContainer = $UI/Customization/Scroll/Margin/Controls
@onready var texture_pack_toggle: CheckButton = controls.get_node("TexturePack")
@onready var unnamed: CheckButton = controls.get_node("Unnamed")

var texture_pack := preload("res://animation_lab/texture_pack.gd").new()

var model: Node3D
var player: AnimationPlayer
var skeleton: Skeleton3D
var overlay_skeleton: Skeleton3D
var clip: StringName = &""
var paused := false
var scrubbing := false
var yaw := 0.45
var pitch := 0.15
var distance := 4.0
var focus := Vector3(0, 1, 0)
var root_at_frame := Vector3.ZERO
var paths: Array[String] = []
var metadata := {}
var bone_lines := ImmediateMesh.new()
var bone_material := StandardMaterial3D.new()


func _ready() -> void:
	texture_pack_toggle.toggled.connect(_apply_texture_pack)
	texture_pack_toggle.disabled = not texture_pack.available()
	unnamed.toggled.connect(func(_value: bool): _filter_animations())
	for slot in ["body", "head", "bracelets"]:
		var dropdown: OptionButton = equipment.get_node(slot.capitalize())
		dropdown.item_selected.connect(_customize.bind(slot))
	equipment.get_node("Cinematic").toggled.connect(_change_render_mode)
	var hair: OptionButton = equipment.get_node("Hair")
	for label in ["Automatic", "Show all", "Hide ponytail", "Hide forelock", "Hide both"]:
		hair.add_item(label)
	hair.item_selected.connect(_change_hair)
	equipment.get_node("Health").value_changed.connect(_change_health)
	equipment.get_node("AppearanceToggle").toggled.connect(func(value: bool): equipment.get_node("Appearance").visible = value)
	var appearance_properties := {"Feather": "darkness_mask_feather", "Reflection": "darkness_reflection", "Normal": "darkness_normal_strength", "Flow": "darkness_flow_speed"}
	for field in appearance_properties:
		equipment.get_node("Appearance/" + field).value_changed.connect(_change_appearance.bind(appearance_properties[field]))
	equipment.get_node("Appearance/Color").color_changed.connect(_change_appearance.bind("darkness_color"))
	equipment.get_node("Appearance/Animate").toggled.connect(_change_appearance.bind("darkness_animate"))
	equipment.get_node("Appearance/Mask").toggled.connect(_change_appearance.bind("darkness_show_mask"))
	equipment.get_node("Appearance/Reset").pressed.connect(_reset_appearance)
	characters.item_selected.connect(_select_character)
	controls.get_node("Refresh").pressed.connect(_refresh_characters)
	filter.text_changed.connect(func(_text: String): _filter_animations())
	animation_list.item_selected.connect(_select_animation)
	play_button.pressed.connect(_toggle_play)
	controls.get_node("Playback/Restart").pressed.connect(_restart)
	controls.get_node("Frame").pressed.connect(_frame_model)
	speed.value_changed.connect(func(_value: float): _apply_speed())
	timeline.drag_started.connect(func():
		scrubbing = true
		_apply_speed())
	timeline.drag_ended.connect(func(_changed: bool):
		scrubbing = false
		_apply_speed())
	timeline.value_changed.connect(_seek)
	bones.toggled.connect(func(_enabled: bool): _draw_bones())
	bone_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	bone_material.albedo_color = Color(0.2, 1.0, 0.8)
	bone_material.no_depth_test = true
	overlay.mesh = bone_lines
	overlay.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_refresh_characters()


func _find_glbs(directory: String, result: Array[String]) -> void:
	var folder := DirAccess.open(directory)
	if folder == null:
		return
	for file in folder.get_files():
		if file.get_extension().to_lower() == "glb":
			result.append(directory.path_join(file))
	for child in folder.get_directories():
		var costume := child.length() == 6 and child.substr(3).is_valid_int() and (child.begins_with("chr1") or child.begins_with("chr72") or child.begins_with("chr74") or (child.begins_with("chr2") and ResourceLoader.exists(characters_directory.path_join("chr2.tscn"))))
		if not child.begins_with(".") and not costume:
			_find_glbs(directory.path_join(child), result)


func _refresh_characters() -> void:
	var previous := ""
	if characters.selected >= 0 and characters.selected < paths.size():
		previous = paths[characters.selected]
	paths.clear()
	_find_glbs(characters_directory, paths)
	for family in ["chr1", "chr2"]:
		var player_scene := characters_directory.path_join(family + ".tscn")
		if ResourceLoader.exists(player_scene):
			paths.append(player_scene)
	var chr100 := characters_directory.path_join("chr100/chr100.glb")
	if ResourceLoader.exists(chr100) and not paths.has(chr100):
		paths.append(chr100)
	paths.sort()
	characters.clear()
	for path in paths:
		var label := path.trim_prefix(characters_directory + "/").get_basename()
		if path.get_base_dir().get_file() == path.get_file().get_basename():
			label = path.get_file().get_basename()
		characters.add_item(label)
	if paths.is_empty():
		_clear_model()
		info.text = "No GLB files in %s. Export a character, then refresh." % characters_directory
		return
	var index := maxi(0, paths.find(previous))
	if previous.is_empty():
		for item in range(paths.size()):
			if paths[item].get_file().get_basename() == initial_character:
				index = item
				break
	characters.select(index)
	_select_character(index)


func _clear_model() -> void:
	texture_pack.clear()
	_set_overlay_skeleton(null)
	customization.hide()
	player = null
	skeleton = null
	clip = &""
	metadata.clear()
	animation_list.clear()
	bone_lines.clear_surfaces()
	if is_instance_valid(model):
		model_root.remove_child(model)
		model.queue_free()
	model = null
	play_button.disabled = true
	timeline.editable = false
	clock.text = "0.00 / 0.00 s"


func _select_character(index: int) -> void:
	_clear_model()
	var path := paths[index]
	if not ResourceLoader.exists(path, "PackedScene"):
		info.text = "Godot import required: %s" % path
		return
	var packed := load(path) as PackedScene
	if packed == null:
		info.text = "Could not load %s" % path
		return
	model = packed.instantiate() as Node3D
	if model == null:
		info.text = "The model does not have a Node3D root."
		return
	model_root.add_child(model)
	var players := model.find_children("*", "AnimationPlayer", true, false)
	var skeletons := model.find_children("*", "Skeleton3D", true, false)
	if not skeletons.is_empty():
		skeleton = skeletons[0] as Skeleton3D
	if not players.is_empty():
		player = players[0] as AnimationPlayer
	var is_player := model.has_method("available_equipment")
	if is_player:
		player = model.get_animation_player()
		_setup_customization()
	var sidecar := path.get_basename() + ".motion.json"
	if is_player:
		var master: String = model.animation_master()
		sidecar = characters_directory.path_join(master + "/" + master + ".motion.json")
	if FileAccess.file_exists(sidecar):
		var data: Variant = JSON.parse_string(FileAccess.get_file_as_string(sidecar))
		if data is Dictionary:
			for segment in data.get("segments", []):
				metadata[segment.get("name", "")] = segment
	filter.clear()
	_filter_animations()
	paused = false
	play_button.disabled = player == null or animation_list.item_count == 0
	timeline.editable = not play_button.disabled
	if not play_button.disabled:
		var initial := 0
		for item in range(animation_list.item_count):
			var candidate := animation_list.get_item_text(item).to_lower()
			if "idle" in candidate or "boredom" in candidate:
				initial = item
				break
		animation_list.select(initial)
		_select_animation(initial)
	else:
		info.text = "This model has no playable animations."
	_apply_texture_pack(texture_pack_toggle.button_pressed)
	_frame_model()


func _apply_texture_pack(enabled: bool) -> void:
	if is_instance_valid(model):
		texture_pack.apply(model, enabled)
	texture_pack_toggle.visible = is_instance_valid(model) and ((model.has_method("available_equipment") and model.character_family == "chr1") or model.scene_file_path.get_file() == "chr100.glb")


func _setup_customization() -> void:
	customization.show()
	var player_equipment: bool = model.character_family == "chr1"
	for field in ["Cinematic", "HeadLabel", "Head", "HairLabel", "Hair", "BraceletsLabel", "Bracelets"]:
		equipment.get_node(field).visible = player_equipment
	for slot in ["body", "head", "bracelets"]:
		var options: Array = model.available_equipment(slot)
		var dropdown: OptionButton = equipment.get_node(slot.capitalize())
		dropdown.clear()
		for asset in options:
			var note: String = model.equipment_source_note(asset)
			dropdown.add_item("None" if asset == "none" else asset + (" [source placeholder]" if not note.is_empty() else ""))
			dropdown.set_item_metadata(dropdown.item_count - 1, asset)
			dropdown.set_item_tooltip(dropdown.item_count - 1, note)
		dropdown.select(maxi(0, options.find(model.get(slot))))
	equipment.get_node("Health").set_value_no_signal(model.health_ratio)
	equipment.get_node("Hair").select(model.head_hair_mode)
	_change_health(model.health_ratio)
	_sync_appearance_controls()
	_sync_render_mode()


func _change_render_mode(enabled: bool) -> void:
	if customization.visible and is_instance_valid(model):
		model.cinematic_render = enabled
		_apply_texture_pack(texture_pack_toggle.button_pressed)
		_sync_render_mode()


func _sync_render_mode() -> void:
	var dropdown: OptionButton = equipment.get_node("Body")
	for i in range(dropdown.item_count):
		var cinematic: String = dropdown.get_item_metadata(i)
		var gameplay: String = model.body_pairs()[cinematic]
		var asset: String = cinematic if model.cinematic_render else gameplay
		var note: String = model.equipment_source_note(asset)
		dropdown.set_item_text(i, asset + (" [source placeholder]" if not note.is_empty() else ""))
		dropdown.set_item_tooltip(i, "Cinematic: %s / Gameplay: %s\n%s" % [cinematic, gameplay, note])
	equipment.get_node("Cinematic").set_pressed_no_signal(model.cinematic_render)
	for field in ["HealthLabel", "Health", "Note", "AppearanceToggle"]:
		equipment.get_node(field).visible = model.character_family == "chr1" and not model.cinematic_render
	equipment.get_node("Appearance").visible = model.character_family == "chr1" and not model.cinematic_render and equipment.get_node("AppearanceToggle").button_pressed


func _customize(index: int, slot: String) -> void:
	if not customization.visible or not is_instance_valid(model):
		return
	var dropdown: OptionButton = equipment.get_node(slot.capitalize())
	model.set(slot, dropdown.get_item_metadata(index))
	_apply_texture_pack(texture_pack_toggle.button_pressed)


func _change_health(value: float) -> void:
	if customization.visible and is_instance_valid(model):
		model.health_ratio = value
		equipment.get_node("HealthLabel").text = "Health: %d%%" % roundi(value * 100.0)


func _change_hair(index: int) -> void:
	if customization.visible and is_instance_valid(model):
		model.head_hair_mode = index


func _change_appearance(value: Variant, property: String) -> void:
	if customization.visible and is_instance_valid(model):
		model.set(property, value)
		_sync_appearance_controls()


func _sync_appearance_controls() -> void:
	var properties := {"Feather": "darkness_mask_feather", "Reflection": "darkness_reflection", "Normal": "darkness_normal_strength", "Flow": "darkness_flow_speed"}
	var labels := {"Feather": "Edge softness", "Reflection": "Reflections", "Normal": "Normal strength", "Flow": "Flow speed"}
	for field in properties:
		var value: float = model.get(properties[field])
		equipment.get_node("Appearance/" + field).set_value_no_signal(value)
		equipment.get_node("Appearance/" + field + "Label").text = "%s: %.3f" % [labels[field], value]
	equipment.get_node("Appearance/Color").color = model.darkness_color
	equipment.get_node("Appearance/Animate").set_pressed_no_signal(model.darkness_animate)
	equipment.get_node("Appearance/Mask").set_pressed_no_signal(model.darkness_show_mask)

	var has_flow_texture: bool = model.darkness_tar_normal != null or model.darkness_noise != null
	equipment.get_node("Appearance/Flow").editable = has_flow_texture
	equipment.get_node("Appearance/Animate").disabled = not has_flow_texture
	equipment.get_node("Appearance/Flow").tooltip_text = "" if has_flow_texture else "Assign a tar normal or noise texture in the chr1 Inspector to enable flow."
	equipment.get_node("Appearance/TextureStatus").text = "Original global textures loaded. Flow animates the tar normals and reflections." if has_flow_texture else "Uses armor normal and reflection maps. Original global tar textures are not assigned; texture flow is disabled."


func _reset_appearance() -> void:
	if not customization.visible or not is_instance_valid(model):
		return
	model.darkness_mask_feather = 0.04
	model.darkness_reflection = 1.0
	model.darkness_normal_strength = 1.0
	model.darkness_flow_speed = 0.1
	model.darkness_color = Color(0.02, 0.015, 0.025, 1.0)
	model.darkness_animate = true
	model.darkness_show_mask = false
	_sync_appearance_controls()


func _update_tar_lighting() -> void:
	if not customization.visible or not is_instance_valid(model):
		return
	var key: DirectionalLight3D = $KeyLight
	var environment: Environment = $WorldEnvironment.environment
	var sky := environment.ambient_light_color * environment.ambient_light_energy
	var light_color := key.light_color * key.light_energy
	var positions := PackedVector3Array([Vector3.ZERO, Vector3.ZERO, Vector3.ZERO])
	var colors := PackedVector3Array([Vector3.ZERO, Vector3.ZERO, Vector3.ZERO])
	var parameters := PackedVector2Array([Vector2.ZERO, Vector2.ZERO, Vector2.ZERO])
	var count := 0
	for light in find_children("*", "OmniLight3D", true, false):
		if count == 3:
			break
		if not light.is_visible_in_tree():
			continue
		positions[count] = light.global_position
		var color: Color = light.light_color * light.light_energy
		colors[count] = Vector3(color.r, color.g, color.b)
		parameters[count] = Vector2(0.0, light.omni_range * 100.0)
		count += 1
	model.set_tar_lighting({
		"color_sky": Vector3(sky.r, sky.g, sky.b),
		"color_ground": Vector3(sky.r, sky.g, sky.b) * 0.2,
		"dir_light_vector": key.global_basis.z,
		"color_dir_light": Vector3(light_color.r, light_color.g, light_color.b),
		"point_light_count": count,
		"point_light_position": positions,
		"point_light_color": colors,
		"point_light_param": parameters,
		"fog_param": Vector3(0.0, 0.0, environment.fog_density / 100.0 if environment.fog_enabled else 0.0),
		"fog_color": Vector3(environment.fog_light_color.r, environment.fog_light_color.g, environment.fog_light_color.b),
	})


func _filter_animations() -> void:
	animation_list.clear()
	if player == null:
		return
	for animation in player.get_animation_list():
		if animation == "RESET" or (not filter.text.is_empty() and not filter.text.to_lower() in animation.to_lower()):
			continue
		var anonymous := String(animation).begins_with("segment_")
		if anonymous and not unnamed.button_pressed:
			continue
		var label := "Unnamed segment " + String(animation).trim_prefix("segment_") if anonymous else String(animation)
		var index := animation_list.add_item(label)
		if anonymous:
			animation_list.set_item_tooltip(index, "Physical motion segment with no name in the source sequence index: " + String(animation))
		animation_list.set_item_metadata(index, animation)
		if animation == clip:
			animation_list.select(index)

	play_button.disabled = animation_list.item_count == 0
	timeline.editable = not play_button.disabled


func _select_animation(index: int) -> void:
	if player == null:
		return
	clip = StringName(animation_list.get_item_metadata(index))
	var animation := player.get_animation(clip)
	timeline.max_value = animation.length
	timeline.set_value_no_signal(0.0)
	player.play(clip)
	player.advance(0.0)
	_apply_speed()
	var bone_count := skeleton.get_bone_count() if skeleton != null else 0
	var details: Dictionary = metadata.get(String(clip), {})
	var ik: Dictionary = details.get("ik", {})
	info.text = "%d animations · %d bones · %d tracks" % [
		player.get_animation_list().size(), bone_count, animation.get_track_count()]
	if not ik.is_empty():
		info.text += "\nIK: %d chains · %d clamped targets" % [
			ik.get("chains", 0), ik.get("clamped_samples", 0)]


func _apply_speed() -> void:
	if player != null:
		player.speed_scale = 0.0 if paused or scrubbing else speed.value
	play_button.text = "Play" if paused else "Pause"


func _toggle_play() -> void:
	if player == null or clip == &"":
		return
	paused = not paused
	if not paused and not player.is_playing():
		player.play(clip)
	_apply_speed()


func _restart() -> void:
	if player != null and clip != &"":
		player.play(clip)
		player.seek(0.0, true)
		_apply_speed()


func _seek(value: float) -> void:
	if player == null or clip == &"":
		return
	if not player.is_playing():
		player.play(clip)
	player.seek(value, true)
	_apply_speed()


func _root_position() -> Vector3:
	if skeleton != null and skeleton.get_bone_count() > 0:
		return skeleton.global_transform * skeleton.get_bone_global_pose(0).origin
	return Vector3.ZERO


func _frame_model() -> void:
	if model == null:
		return
	var bounds := AABB()
	var first := true
	for instance in model.find_children("*", "MeshInstance3D", true, false):
		var mesh := instance as MeshInstance3D
		if mesh.mesh == null or not mesh.is_visible_in_tree():
			continue
		var box: AABB = mesh.global_transform * mesh.get_aabb()
		bounds = box if first else bounds.merge(box)
		first = false
	focus = bounds.get_center() if not first else Vector3(0, 1, 0)
	distance = maxf(1.0, bounds.size.length() * 1.5)
	ground.position.y = bounds.position.y - 0.01
	root_at_frame = _root_position()
	yaw = 0.45
	pitch = 0.15


func _process(_delta: float) -> void:
	_update_tar_lighting()
	if player != null and clip != &"":
		if not player.is_playing() and not paused and not scrubbing:
			if loop.button_pressed:
				player.play(clip)
			else:
				paused = true
				_apply_speed()
		var time := player.current_animation_position if player.is_playing() else timeline.max_value
		if not scrubbing:
			timeline.set_value_no_signal(time)
		clock.text = "%.2f / %.2f s  ·  frame %d" % [time, timeline.max_value, roundi(time * 30)]
	var target := focus
	if follow.button_pressed:
		target += _root_position() - root_at_frame
	var direction := Vector3(sin(yaw) * cos(pitch), sin(pitch), cos(yaw) * cos(pitch))
	camera.position = target + direction * distance
	camera.h_offset = 0.0 if customization.visible else -distance * 0.19
	camera.far = maxf(200.0, distance * 20.0)
	camera.look_at(target)
	_sync_overlay_skeleton()
	if not bones.button_pressed:
		bone_lines.clear_surfaces()


func _sync_overlay_skeleton() -> void:
	var target := skeleton
	if is_instance_valid(model):
		# The hidden animation master does not run cloth/spring modifiers.
		# Follow the skeleton actually used by a visible skinned mesh.
		for instance in model.find_children("*", "MeshInstance3D", true, false):
			var mesh := instance as MeshInstance3D
			if mesh.skin == null or not mesh.is_visible_in_tree():
				continue
			var rig := mesh.get_node_or_null(mesh.skeleton) as Skeleton3D
			if rig != null:
				target = rig
				break
	_set_overlay_skeleton(target)


func _set_overlay_skeleton(target: Skeleton3D) -> void:
	if overlay_skeleton == target:
		return
	if is_instance_valid(overlay_skeleton):
		overlay_skeleton.skeleton_updated.disconnect(_draw_bones)
	overlay_skeleton = target
	if is_instance_valid(overlay_skeleton):
		# This signal runs after all modifiers, before Godot restores the
		# animation poses. Reading in _process misses the physical pose.
		overlay_skeleton.skeleton_updated.connect(_draw_bones)
	_draw_bones()


func _draw_bones() -> void:
	bone_lines.clear_surfaces()
	if not bones.button_pressed or not is_instance_valid(overlay_skeleton):
		return
	if not overlay_skeleton.is_inside_tree() or overlay_skeleton.get_bone_count() < 2:
		return
	var to_overlay := overlay.global_transform.affine_inverse() * overlay_skeleton.global_transform
	bone_lines.surface_begin(Mesh.PRIMITIVE_LINES, bone_material)
	for index in range(overlay_skeleton.get_bone_count()):
		var parent := overlay_skeleton.get_bone_parent(index)
		if parent >= 0:
			bone_lines.surface_add_vertex(to_overlay * overlay_skeleton.get_bone_global_pose(parent).origin)
			bone_lines.surface_add_vertex(to_overlay * overlay_skeleton.get_bone_global_pose(index).origin)
	bone_lines.surface_end()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_RIGHT:
		yaw -= event.relative.x * 0.006
		pitch = clampf(pitch + event.relative.y * 0.006, -1.4, 1.4)
	elif event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			distance = maxf(0.1, distance * 0.9)
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			distance *= 1.1
