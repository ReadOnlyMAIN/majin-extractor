extends Node3D
## Standalone motion review: dropdown, scrubber, pause, right-drag orbit, wheel zoom.
## GLTFDocument preserves source keys and allows reviewing a freshly copied GLB
## before the editor has imported it. Normal editor import uses the companion .import.

@export_file("*.glb") var model_path: String
@export var initial_animation: String

var player: AnimationPlayer
var skeleton: Skeleton3D
var camera: Camera3D
var selector: OptionButton
var slider: HSlider
var timer_label: Label
var paused := false
var dragging_time := false
var yaw := 0.45
var pitch := 0.12
var distance := 3.0
var focus := Vector3(0, 1, 0)
var bind_root := Vector3.ZERO
var capture_path := ""

func _ready():
    var document := GLTFDocument.new()
    var state := GLTFState.new()
    if document.append_from_file(model_path, state) != OK:
        push_error("Cannot decode preview GLB: %s" % model_path)
        return
    var model := document.generate_scene(state, 30.0, false, false)
    add_child(model)
    var players := model.find_children("*", "AnimationPlayer", true, false)
    var skeletons := model.find_children("*", "Skeleton3D", true, false)
    if players.is_empty() or skeletons.is_empty():
        push_error("Preview requires an animated skeleton")
        return
    player = players[0]
    skeleton = skeletons[0]
    bind_root = skeleton.get_bone_global_pose(0).origin
    var bounds := AABB()
    var first := true
    for mesh in model.find_children("*", "MeshInstance3D", true, false):
        var box: AABB = mesh.global_transform * mesh.get_aabb()
        bounds = box if first else bounds.merge(box)
        first = false
    focus = bounds.get_center()
    distance = maxf(1.0, bounds.size.length() * 1.5)
    camera = Camera3D.new()
    camera.fov = 42.0
    camera.far = maxf(100.0, distance*20)
    add_child(camera)
    camera.current = true
    var environment := WorldEnvironment.new()
    environment.environment = Environment.new()
    environment.environment.background_mode = Environment.BG_COLOR
    environment.environment.background_color = Color(0.12, 0.14, 0.17)
    environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
    environment.environment.ambient_light_color = Color.WHITE
    environment.environment.ambient_light_energy = 0.7
    add_child(environment)
    var light := DirectionalLight3D.new()
    light.rotation_degrees = Vector3(-45, -25, 0)
    light.light_energy = 1.5
    add_child(light)
    var layer := CanvasLayer.new()
    add_child(layer)
    var panel := PanelContainer.new()
    panel.position = Vector2(16, 16)
    panel.custom_minimum_size = Vector2(500, 100)
    layer.add_child(panel)
    var column := VBoxContainer.new()
    panel.add_child(column)
    selector = OptionButton.new()
    for animation in player.get_animation_list():
        selector.add_item(animation)
    column.add_child(selector)
    selector.item_selected.connect(_select_animation)
    slider = HSlider.new()
    slider.step = 1.0/30.0
    slider.drag_started.connect(func(): dragging_time = true)
    slider.drag_ended.connect(func(_changed): dragging_time = false)
    slider.value_changed.connect(func(value):
        if dragging_time or paused:
            player.seek(value, true))
    column.add_child(slider)
    var row := HBoxContainer.new()
    column.add_child(row)
    var pause_button := Button.new()
    pause_button.text = "Pause / Play"
    pause_button.pressed.connect(func():
        paused = not paused
        player.speed_scale = 0.0 if paused else 1.0)
    row.add_child(pause_button)
    timer_label = Label.new()
    row.add_child(timer_label)
    var capture_time := 0.0
    for argument in OS.get_cmdline_user_args():
        if argument.begins_with("--capture="):
            capture_path = argument.trim_prefix("--capture=")
        elif argument.begins_with("--animation="):
            initial_animation = argument.trim_prefix("--animation=")
        elif argument.begins_with("--time="):
            capture_time = argument.trim_prefix("--time=").to_float()
    var initial := 0
    for index in range(selector.item_count):
        if selector.get_item_text(index) == initial_animation:
            initial = index
    selector.select(initial)
    _select_animation(initial)
    if not capture_path.is_empty():
        player.seek(capture_time, true)
        player.speed_scale = 0.0
        await get_tree().process_frame
        await get_tree().process_frame
        await RenderingServer.frame_post_draw
        get_viewport().get_texture().get_image().save_png(capture_path)
        get_tree().quit()

func _select_animation(index: int):
    var animation_name := selector.get_item_text(index)
    player.play(animation_name)
    player.seek(0.0, true)
    slider.max_value = player.get_animation(animation_name).length

func _process(_delta):
    if player == null:
        return
    var time := player.current_animation_position
    if not paused and not dragging_time and player.speed_scale > 0 and time >= slider.max_value and slider.max_value > 0:
        player.seek(0.0, true)
    if not dragging_time:
        slider.set_value_no_signal(time)
    timer_label.text = "%.2f / %.2f s | Right-drag: orbit | Wheel: zoom" % [time, slider.max_value]
    var target := focus + skeleton.get_bone_global_pose(0).origin - bind_root
    var direction := Vector3(sin(yaw)*cos(pitch), sin(pitch), cos(yaw)*cos(pitch))
    camera.position = target + direction*distance
    camera.look_at(target)

func _unhandled_input(event):
    if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_RIGHT:
        yaw -= event.relative.x*0.006
        pitch = clampf(pitch + event.relative.y*0.006, -1.4, 1.4)
    elif event is InputEventMouseButton and event.pressed:
        if event.button_index == MOUSE_BUTTON_WHEEL_UP:
            distance = maxf(0.1, distance*0.9)
        elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
            distance *= 1.1
