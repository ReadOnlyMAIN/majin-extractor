extends RefCounted
## Validate editor-visible animation bindings and AnimationTree pose evaluation.

static func run(root: Node) -> Dictionary:
	var scene := load("res://original_files/decoded/chara/chr1.tscn") as PackedScene
	var player := scene.instantiate()
	var reference := scene.instantiate()
	var failures: Array[String] = []
	var animator: AnimationPlayer = player.get_animation_player()
	var tree: AnimationTree = player.get_animation_tree()
	if animator == null or tree == null:
		player.free()
		reference.free()
		return {"passed":false,"failures":["Animation nodes unavailable before ready"]}
	var count := animator.get_animation_list().size()
	if count == 0 or tree.get_node(tree.anim_player) != animator:
		failures.append("Serialized AnimationTree cannot resolve animation library")
	root.add_child(player)
	root.add_child(reference)
	player.position.x = 100.0
	reference.position.x = 102.0
	if player.find_children("AnimationRig", "Node3D", false, false).size() != 1:
		failures.append("Duplicate animation rig")
	var clip := "c100_0010_autohold_b_00"
	var animation := AnimationNodeAnimation.new()
	animation.animation = clip
	tree.tree_root = animation
	tree.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
	tree.active = true
	tree.advance(0.0)
	tree.advance(0.5)
	var reference_animator: AnimationPlayer = reference.get_animation_player()
	reference_animator.play(clip)
	reference_animator.seek(0.5, true)
	reference_animator.pause()
	var maximum_error := 0.0
	for i in range(player._skeleton.get_bone_count()):
		var actual: Transform3D = player._skeleton.get_bone_pose(i)
		var expected: Transform3D = reference._skeleton.get_bone_pose(i)
		maximum_error = maxf(maximum_error, actual.origin.distance_to(expected.origin))
		for column in range(3):
			maximum_error = maxf(maximum_error, actual.basis[column].distance_to(expected.basis[column]))
	if maximum_error > 0.0001:
		failures.append("AnimationTree pose differs from AnimationPlayer")
	player.queue_free()
	reference.queue_free()
	return {"passed":failures.is_empty(),"failures":failures,"animations_before_ready":count,"maximum_pose_error":maximum_error}
