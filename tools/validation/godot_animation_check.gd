extends SceneTree
## Engine-side GLB import, finite-pose and source IK target checks.
## Invoked by check_godot_animation.py; uses no project addons or import script.

func _initialize():
    call_deferred("run")

func fail(message: String):
    push_error(message)
    quit(1)

func run():
    var arguments := OS.get_cmdline_user_args()
    var specification = JSON.parse_string(FileAccess.get_file_as_string(arguments[1]))
    var scene: Node3D
    var editor_import := arguments.size() > 3 and arguments[3] == "editor"
    if editor_import:
        var packed := load("res://model.glb") as PackedScene
        if packed == null:
            fail("Imported GLB scene missing")
            return
        scene = packed.instantiate()
    else:
        var document := GLTFDocument.new()
        var state := GLTFState.new()
        var status := document.append_from_file(arguments[0], state)
        if status != OK:
            fail("GLB append failed: %s" % status)
            return
        # Match the editor's GLTF importer, which also passes false here.
        scene = document.generate_scene(state, float(specification.frame_rate), false, false)
    root.add_child(scene)
    var players := scene.find_children("*", "AnimationPlayer", true, false)
    var skeletons := scene.find_children("*", "Skeleton3D", true, false)
    if players.size() != 1 or skeletons.size() != 1:
        fail("Expected one AnimationPlayer and one Skeleton3D")
        return
    var player := players[0] as AnimationPlayer
    var skeleton := skeletons[0] as Skeleton3D
    var max_error := 0.0
    var unclamped_max_error := 0.0
    var samples := 0
    for item in specification.samples:
        if not player.has_animation(item.animation):
            fail("Animation missing: %s" % item.animation)
            return
        player.play(item.animation)
        player.seek(float(item.time), true)
        for bone in range(skeleton.get_bone_count()):
            if not skeleton.get_bone_global_pose(bone).is_finite():
                fail("Non-finite bone pose in %s" % item.animation)
                return
        for target in item.targets:
            var bone := skeleton.find_bone(target.bone)
            if bone < 0:
                fail("IK bone missing: %s" % target.bone)
                return
            var expected := Vector3(target.position[0], target.position[1], target.position[2])
            var actual := skeleton.get_bone_global_pose(bone).origin
            var error := actual.distance_to(expected)
            max_error = maxf(max_error, error)
            if int(item.source_clamped_samples) == 0:
                unclamped_max_error = maxf(unclamped_max_error, error)
            if error > float(item.tolerance):
                fail("IK target error %s in %s/%s at t=%s exceeds %s; actual=%s expected=%s" % [error, item.animation, target.bone, item.time, item.tolerance, actual, expected])
                return
        samples += 1
    var result := {"godot_version": Engine.get_version_info().string,
        "bones": skeleton.get_bone_count(), "animations": player.get_animation_list().size(),
        "editor_import": editor_import,
        "pose_samples": samples, "max_ik_target_error_meters": max_error,
        "unclamped_max_ik_target_error_meters": unclamped_max_error}
    var output := FileAccess.open(arguments[2], FileAccess.WRITE)
    output.store_string(JSON.stringify(result, "  ") + "\n")
    output.close()
    print("GODOT_VALIDATION ", JSON.stringify(result))
    scene.queue_free()
    quit(0)
