extends SceneTree
## Run by test_player_physics.py when GODOT_BIN is supplied.
## Checks imported ownership, serialization, finite poses and secondary motion.
var samples := 0
var max_angle := 0.0
func _initialize():
    create_timer(10.0).timeout.connect(func():
        push_error("Spring validation timed out")
        quit(1)
    )
    run.call_deferred()
func run():
    var armor = load("res://player_armor.gd")
    assert(armor != null and armor.can_instantiate())
    var spec = JSON.parse_string(FileAccess.get_file_as_string("res://spec.json"))
    var scene := Node3D.new()
    scene.name = "Character"
    var skeleton := Skeleton3D.new()
    skeleton.name = "Skeleton3D"
    scene.add_child(skeleton)
    skeleton.owner = scene
    for j in spec.joints:
        skeleton.add_bone(j.name)
        var i := skeleton.get_bone_count() - 1
        if j.parent != null:
            skeleton.set_bone_parent(i, int(j.parent))
        var q = j.rotation
        var t = j.translation
        var rest := Transform3D(Basis(Quaternion(q[0], q[1], q[2], q[3])), Vector3(t[0], t[1], t[2]) * 0.01)
        skeleton.set_bone_rest(i, rest)
        skeleton.set_bone_pose(i, rest)
    var importer = load("res://assign_materials.gd").new()
    importer._spring_bone_chains = spec.chains
    importer._spring_bone_collisions = spec.get("collisions", [])
    importer._setup_spring_bones(scene)
    importer._setup_spring_bones(scene)
    assert(skeleton.has_node("MajinSecondaryMotion"))
    assert(skeleton.get_child_count() == 2)
    var packed := PackedScene.new()
    assert(packed.pack(scene) == OK)
    assert(ResourceSaver.save(packed, "res://packed.tscn") == OK)
    scene.free()
    scene = load("res://packed.tscn").instantiate()
    skeleton = scene.get_node("Skeleton3D")
    root.add_child(scene)
    var spring = skeleton.get_node("MajinSecondaryMotion")
    assert(spring.external_force.is_zero_approx())
    assert(spring.get_child_count() == spec.collisions.size())
    assert(spring.setting_count == spec.chains.size())
    for i in spec.chains.size():
        assert(not spring.are_all_child_collisions_enabled(i))
        assert(spring.get_collision_count(i) == spec.chains[i].collision_names.size())
        assert(spring.get_root_bone_name(i) == spec.chains[i].root_bone)
        assert(is_equal_approx(spring.get_end_bone_length(i), spec.chains[i].end_length))
        assert(spring.get_gravity_direction(i).is_equal_approx(Vector3.DOWN))
        assert(spring.is_config_individual(i))
        assert(spring.get_joint_count(i) > 0)
        var base_gravity := float(spec.chains[i].gravity)
        if spec.chains[i].get("gravity_mode", "external_force") != "per_joint":
            base_gravity += 1.0
        for joint in spring.get_joint_count(i):
            var expected: Dictionary = {}
            var bone_name: String = spring.get_joint_bone_name(i, joint)
            for entry in spec.chains[i].get("joint_settings", []):
                if entry.bone == bone_name:
                    expected = entry
            assert(is_equal_approx(spring.get_joint_gravity(i, joint), float(expected.get("gravity", base_gravity))))
            assert(spring.get_joint_gravity_direction(i, joint).is_equal_approx(Vector3.DOWN))
            assert(is_equal_approx(spring.get_joint_stiffness(i, joint), float(expected.get("stiffness", spec.chains[i].stiffness))))
            assert(is_equal_approx(spring.get_joint_drag(i, joint), float(expected.get("drag", spec.chains[i].drag))))
            assert(is_equal_approx(spring.get_joint_radius(i, joint), float(expected.get("radius", spec.chains[i].get("radius", 0.02)))))
    var hair := skeleton.find_bone("bone_190")
    var face := skeleton.find_bone("bone_120")
    spring.modification_processed.connect(func():
        samples += 1
        var rotation := skeleton.get_bone_pose_rotation(hair)
        max_angle = maxf(max_angle, rotation.angle_to(skeleton.get_bone_rest(hair).basis.get_rotation_quaternion()))
        assert(skeleton.get_bone_pose(face).is_equal_approx(skeleton.get_bone_rest(face)))
        for i in skeleton.get_bone_count():
            assert(skeleton.get_bone_global_pose(i).is_finite())
    )
    var updater := skeleton.get_node("MajinSpringCollisionUpdate")
    var capsule := spring.get_node("Capsule")
    var face_rest := skeleton.get_bone_rest(face)
    var moved_face := face_rest
    moved_face.origin.y += 0.06
    skeleton.set_bone_pose(face, moved_face)
    updater.call("update_collisions", skeleton, spring)
    var point_a := skeleton.get_bone_global_pose(skeleton.find_bone("bone_110")).origin
    var point_b := skeleton.get_bone_global_pose(face).origin
    assert(is_equal_approx(capsule.height, point_a.distance_to(point_b) + 0.02))
    assert(capsule.position.is_equal_approx((point_a + point_b) * 0.5))
    skeleton.set_bone_pose(face, face_rest)
    for frame in 120:
        # Like chr1 pose transfer, reset animation input before secondary motion.
        for i in skeleton.get_bone_count():
            skeleton.set_bone_pose(i, skeleton.get_bone_rest(i))
        scene.position.z = sin(frame * 0.15) * 0.1
        await process_frame
    assert(samples > 0)
    assert(max_angle > 0.0001, "Spring did not move")
    print("SPRING_VALIDATION chains=%d samples=%d max_hair_angle=%f" % [spring.setting_count, samples, max_angle])
    scene.free()
    importer = null
    quit()
