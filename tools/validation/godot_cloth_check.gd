extends SceneTree

func _initialize() -> void:
	run.call_deferred()

func run() -> void:
	var skeleton := Skeleton3D.new()
	root.add_child(skeleton)
	var columns: Array = []
	for c in 3:
		var names: Array = []
		for row in 4:
			var name := "c%d_r%d" % [c, row]
			var bone := skeleton.get_bone_count()
			skeleton.add_bone(name)
			if row > 0:
				skeleton.set_bone_parent(bone, bone - 1)
			var rest := Transform3D(Basis.IDENTITY, Vector3(0.15 if row > 0 else 0.0, 0.0, c * 0.15 if row == 0 else 0.0))
			skeleton.set_bone_rest(bone, rest)
			skeleton.set_bone_pose(bone, rest)
			names.append(name)
		columns.append(names)
	var solver := SkeletonModifier3D.new()
	solver.set_script(load("res://cloth_constraints.gd"))
	solver.surfaces = [{"name": "test", "bone_columns": columns,
		"tail_offsets": [[0.1, 0, 0], [0.1, 0, 0], [0.1, 0, 0]],
		"kt": 70.0, "ks": 50.0, "kb": 50.0, "mass": 0.3,
		"air_damping": 0.15, "update_delta": 0.005, "particle_radius": 0.03,
		"collision_names": ["test_capsule"], "max_strain": 0.02}]
	skeleton.add_child(solver)
	assert(solver.gravity_acceleration.is_equal_approx(Vector3(0, -9.8, 0)))
	assert(solver.external_force.is_zero_approx())
	# Measure a free particle's first gravity step at two masses. Disable
	# links here so gravity is measured independently of cloth constraints.
	var displacements: Array = []
	for mass in [0.3, 0.9]:
		for bone in skeleton.get_bone_count():
			skeleton.set_bone_pose(bone, skeleton.get_bone_rest(bone))
		solver.reset_simulation()
		solver.surfaces[0].mass = mass
		solver._build(skeleton)
		var free_state = solver._states[0]
		free_state.edges.clear()
		var before: Vector3 = free_state.positions[4]
		solver.simulate(skeleton, 0.005)
		var displacement: Vector3 = free_state.positions[4] - before
		assert(absf(displacement.y - (-9.8 * 0.005 * 0.005)) < 0.0000001)
		displacements.append(displacement.y)
	assert(is_equal_approx(displacements[0], displacements[1]))
	# Air resistance must dissipate both normal and tangential movement.
	# With links/gravity disabled, compare first-step velocity to the
	# directional exponential drag law at the default preview settings.
	assert(is_equal_approx(solver.air_resistance_scale, 4.0))
	assert(is_equal_approx(solver.tangential_air_ratio, 0.15))
	solver.gravity_acceleration = Vector3.ZERO
	solver.surfaces[0].mass = 0.3
	for direction in [Vector3.UP, Vector3.RIGHT]:
		for bone in skeleton.get_bone_count():
			skeleton.set_bone_pose(bone, skeleton.get_bone_rest(bone))
		solver.reset_simulation()
		solver._build(skeleton)
		var air_state = solver._states[0]
		air_state.edges.clear()
		for i in air_state.positions.size():
			if not air_state.pinned[i]:
				air_state.previous[i] = air_state.positions[i] - direction * 0.005
		var before: Vector3 = air_state.positions[4]
		solver.simulate(skeleton, 0.005)
		var velocity: Vector3 = (air_state.positions[4] - before) / 0.005
		var incidence := 1.0 if direction == Vector3.UP else 0.15
		var expected := exp(-0.15 * 4.0 * incidence * 0.005 / 0.3)
		assert(absf(velocity.dot(direction) - expected) < 0.0001)
		assert(velocity.dot(direction) > 0.0 and velocity.length() < 1.0)
	solver.gravity_acceleration = Vector3(0, -9.8, 0)
	for bone in skeleton.get_bone_count():
		skeleton.set_bone_pose(bone, skeleton.get_bone_rest(bone))
	solver.surfaces[0].mass = 0.3
	solver.reset_simulation()
	solver.simulate(skeleton, 0.005)
	var state = solver._states[0]
	var link = state.edges[0]
	var expected_k := 0.01 * 70.0 / 0.15
	assert(is_equal_approx(link.alpha, 1.0 / (expected_k * 0.005 * 0.005)), "raw coefficient was used without unit conversion")
	var saved_positions = state.positions.duplicate()
	state.positions[link.b] = Vector3(0.1515, 0, 0)
	link.impulse = 0.0
	solver._solve_edge(state, link, false)
	var tension_correction: float = 0.1515 - state.positions[link.b].x
	state.positions[link.b] = Vector3(0.1485, 0, 0)
	link.impulse = 0.0
	solver._solve_edge(state, link, false)
	var compression_correction: float = state.positions[link.b].x - 0.1485
	assert(absf(compression_correction / tension_correction - 0.5) < 0.03)
	state.positions = saved_positions
	var start: Vector3 = state.positions[4]
	# Force columns apart; the structural constraints must restore their width.
	state.positions[14] += Vector3(0, 0, 0.3)
	state.previous[14] = state.positions[14]
	for step in 200:
		solver.simulate(skeleton, 0.005)
		for p in state.positions:
			assert(p.is_finite())
	var metrics: Array = solver.constraint_metrics()
	assert(metrics[0].max_extension < 0.04, str(metrics))
	assert(state.positions[4].y < start.y - 0.001, "gravity had no effect")
	for column in state.columns:
		var i: int = column[0]
		assert(state.positions[i].is_equal_approx(state.rest[i]), "anchor moved")
	# Capsule projection, including the radius of the cloth particle.
	var obstacle = solver.get_script().Obstacle.new()
	obstacle.a = Vector3.ZERO
	obstacle.axis = Vector3(0, 0.3, 0)
	obstacle.inverse_length_squared = 1.0 / 0.09
	obstacle.radius = 0.1
	state.positions[1] = Vector3(0.01, 0.15, 0)
	solver._collide(state, [obstacle])
	assert(is_equal_approx(state.positions[1].x, 0.1))
	assert(state.positions[0].is_equal_approx(state.rest[0]))
	# An embedded anchor must not force the first link to stretch just to
	# satisfy the extra cloth thickness. Preserve the body's CSR radius.
	solver.collisions = [{"name": "test_capsule", "shape": "sphere", "bone_a": "c0_r0", "point_a": [0, 0, 0], "radius": 0.14}]
	var obstacles = solver._world_colliders(skeleton, state, solver._animated_positions(skeleton, state))
	assert(obstacles.size() == 1)
	assert(is_equal_approx(obstacles[0].radii[1], 0.15 * 0.98))
	assert(obstacles[0].radii[1] >= 0.14)
	solver.collisions = []
	# A long pause resets velocities; no accumulated catch-up explosion.
	solver.simulate(skeleton, 1.0)
	for p in state.positions:
		assert(p.is_finite())
	var packed := PackedScene.new()
	solver.owner = skeleton
	assert(packed.pack(skeleton) == OK)
	var restored: Skeleton3D = packed.instantiate()
	assert(restored.get_child(0).surfaces.size() == 1)
	assert(restored.get_child(0).gravity_acceleration.is_equal_approx(Vector3(0, -9.8, 0)))
	assert(restored.get_child(0).external_force.is_zero_approx())
	assert(is_equal_approx(restored.get_child(0).air_resistance_scale, 4.0))
	assert(is_equal_approx(restored.get_child(0).tangential_air_ratio, 0.15))
	assert(restored.get_child(0)._states.is_empty(), "runtime state was serialized")
	print("CLOTH_VALIDATION ", metrics, " gravity_y=", state.positions[4].y)
	restored.free()
	skeleton.free()
	quit()
