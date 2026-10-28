@tool
extends SkeletonModifier3D
## XPBD conversion of SSR CLOTH groups. Scalar stiffness uses the decoded
## relative-strain law, including half response in compression. The graph
## and ks/kb geometric projections remain approximations of the game solver.
## Angular table indexing is unknown and deliberately not guessed.

@export var surfaces: Array = []
@export var collisions: Array = []
## Metric acceleration, independent of particle mass. Original default: 980 cm/s^2.
@export var gravity_acceleration := Vector3(0, -9.8, 0)
## Additional physical forces (e.g. wind), separate from native spring bias.
@export var external_force := Vector3.ZERO
## Preview tuning over decoded SSR damping: dissipate repeated swings.
@export_range(0.0, 10.0, 0.1) var air_resistance_scale := 4.0
## A coarse bone grid misses folds/edges that also resist tangential motion.
@export_range(0.0, 1.0, 0.01) var tangential_air_ratio := 0.15
class Link:
	var a: int
	var b: int
	var length: float
	var alpha: float
	var impulse := 0.0
	var wa: float
	var wb: float
	var structural: bool
	var extension_limit: float

class ClothState:
	var config: Dictionary
	var bones := PackedInt32Array()
	var rest := PackedVector3Array()
	var positions := PackedVector3Array()
	var previous := PackedVector3Array()
	var pinned := PackedByteArray()
	var edges: Array[Link] = []
	var columns: Array[PackedInt32Array] = []
	var anchor_indices := PackedInt32Array()
	var anchor_reach := PackedFloat32Array()

class Obstacle:
	var a: Vector3
	var axis: Vector3
	var inverse_length_squared: float
	var radius: float
	var radii := PackedFloat32Array()

@export var iterations := 16
var _states: Array = []
var _accumulator := 0.0
var simulation_steps := 0

func _validate_property(property: Dictionary) -> void:
	if property.name in ["_states", "_accumulator", "simulation_steps"]:
		property.usage = PROPERTY_USAGE_NONE

func _enter_tree() -> void:
	reset_simulation()

func reset_simulation() -> void:
	_states.clear()
	_accumulator = 0.0

func _process_modification() -> void:
	var skeleton := get_skeleton()
	if skeleton != null and skeleton.is_inside_tree():
		simulate(skeleton, get_process_delta_time())

func _vec(value: Array) -> Vector3:
	return Vector3(value[0], value[1], value[2])

func _edge(state: ClothState, a: int, b: int, k: float, structural: bool, extension_limit: float = 0.02) -> void:
	var edge := Link.new()
	edge.a = a
	edge.b = b
	edge.length = state.rest[a].distance_to(state.rest[b])
	var dt := maxf(float(state.config.update_delta), 0.001)
	# Original F_source = k * (L-L0)/L0. Transform force as well as
	# position to metric units; raw k is not a Godot distance stiffness.
	var source_scale := float(state.config.get("source_scale", 0.01))
	var metric_k := source_scale * k / maxf(edge.length, 0.000001)
	edge.alpha = 1.0 / (maxf(metric_k, 0.001) * dt * dt)
	edge.extension_limit = extension_limit
	var inverse_mass := 1.0 / maxf(float(state.config.mass), 0.0001)
	edge.wa = 0.0 if state.pinned[a] else inverse_mass
	edge.wb = 0.0 if state.pinned[b] else inverse_mass
	edge.structural = structural
	state.edges.append(edge)

func _build(skeleton: Skeleton3D) -> void:
	for config in surfaces:
		var state := ClothState.new()
		state.config = config
		var valid := true
		for column_index in config.bone_columns.size():
			var column := PackedInt32Array()
			for bone_name in config.bone_columns[column_index]:
				var bone := skeleton.find_bone(bone_name)
				if bone < 0:
					valid = false
					break
				column.append(state.bones.size())
				state.bones.append(bone)
				state.rest.append(skeleton.get_bone_global_rest(bone).origin)
				state.pinned.append(1 if column.size() == 1 else 0)
			if not valid:
				break
			# A virtual tip lets the final weighted bone rotate too.
			var tail: int = state.bones[column[-1]]
			column.append(state.bones.size())
			state.bones.append(-1)
			state.rest.append(skeleton.get_bone_global_rest(tail) * _vec(config.tail_offsets[column_index]))
			state.pinned.append(0)
			state.columns.append(column)
		if not valid:
			push_warning("majin cloth: missing source bone in " + str(config.name))
			continue
		for column in state.columns:
			var reach := 0.0
			for row in column.size():
				if row > 0:
					reach += state.rest[column[row - 1]].distance_to(state.rest[column[row]])
				state.anchor_indices.append(column[0])
				state.anchor_reach.append(reach)
			for row in range(column.size() - 1):
				_edge(state, column[row], column[row + 1], config.kt, true, 0.001)
			for row in range(column.size() - 2):
				_edge(state, column[row], column[row + 2], config.kb, false)
		for c in range(state.columns.size() - 1):
			var left: PackedInt32Array = state.columns[c]
			var right: PackedInt32Array = state.columns[c + 1]
			for row in left.size():
				_edge(state, left[row], right[row], config.kt, true)
			for row in range(left.size() - 1):
				_edge(state, left[row], right[row + 1], config.ks, false)
				_edge(state, right[row], left[row + 1], config.ks, false)
		_states.append(state)
		_initialize_positions(skeleton, state)

func _animated_positions(skeleton: Skeleton3D, state: ClothState) -> PackedVector3Array:
	var result := PackedVector3Array()
	for column_index in state.columns.size():
		var column: PackedInt32Array = state.columns[column_index]
		for row in range(column.size() - 1):
			result.append(skeleton.global_transform * skeleton.get_bone_global_pose(state.bones[column[row]]).origin)
		var tail: int = state.bones[column[-2]]
		result.append(skeleton.global_transform * (skeleton.get_bone_global_pose(tail) * _vec(state.config.tail_offsets[column_index])))
	return result

func _initialize_positions(skeleton: Skeleton3D, state: ClothState) -> void:
	state.positions = _animated_positions(skeleton, state)
	state.previous = state.positions.duplicate()

func _world_colliders(skeleton: Skeleton3D, state: ClothState, targets: PackedVector3Array) -> Array[Obstacle]:
	var config := state.config
	var result: Array[Obstacle] = []
	for entry in collisions:
		if not entry.name in config.collision_names:
			continue
		var bone_a := skeleton.find_bone(entry.bone_a)
		if bone_a < 0:
			continue
		var a := skeleton.global_transform * (skeleton.get_bone_global_pose(bone_a) * _vec(entry.point_a))
		var b := a
		if entry.shape == "capsule":
			var bone_b := skeleton.find_bone(entry.bone_b)
			if bone_b < 0:
				continue
			b = skeleton.global_transform * (skeleton.get_bone_global_pose(bone_b) * _vec(entry.point_b))
		var obstacle := Obstacle.new()
		obstacle.a = a
		obstacle.axis = b - a
		obstacle.inverse_length_squared = 1.0 / maxf(obstacle.axis.length_squared(), 0.00000001)
		obstacle.radius = float(entry.radius) + float(config.particle_radius)
		for i in state.positions.size():
			var anchor := targets[state.anchor_indices[i]]
			var t := clampf((anchor - obstacle.a).dot(obstacle.axis) * obstacle.inverse_length_squared, 0.0, 1.0)
			var clearance := anchor.distance_to(obstacle.a + obstacle.axis * t)
			# Embedded attachments can make the conservative cloth padding
			# unreachable. Reduce only that padding near the anchor, never
			# the original body's CSR radius, instead of stretching cloth.
			var reachable := clearance + state.anchor_reach[i] * 0.98
			obstacle.radii.append(maxf(float(entry.radius), minf(obstacle.radius, reachable)))
		result.append(obstacle)
	return result

func _collide(state: ClothState, obstacles: Array) -> void:
	for i in state.positions.size():
		if state.pinned[i]:
			continue
		var p: Vector3 = state.positions[i]
		for obstacle: Obstacle in obstacles:
			var t := clampf((p - obstacle.a).dot(obstacle.axis) * obstacle.inverse_length_squared, 0.0, 1.0)
			var closest := obstacle.a + obstacle.axis * t
			var offset := p - closest
			var distance := offset.length()
			var radius := obstacle.radii[i] if not obstacle.radii.is_empty() else obstacle.radius
			if distance < radius:
				var normal := offset / distance if distance > 0.000001 else Vector3.BACK
				p = closest + normal * radius
		state.positions[i] = p

func _solve_edge(state: ClothState, edge: Link, hard_limit: bool) -> void:
	var a: int = edge.a
	var b: int = edge.b
	var delta: Vector3 = state.positions[b] - state.positions[a]
	var length := delta.length()
	if length < 0.000001:
		return
	var wa := edge.wa
	var wb := edge.wb
	if wa + wb == 0.0:
		return
	var error := length - float(edge.length)
	var alpha := edge.alpha * (2.0 if error < 0.0 else 1.0)
	var impulse := (-error - alpha * edge.impulse) / (wa + wb + alpha)
	if hard_limit:
		# Original longitudinal correction caps extension at 0.1%; it
		# does not impose a lower bound that would prevent cloth folding.
		var limit := edge.length * edge.extension_limit
		error = maxf(error - limit, 0.0)
		impulse = -error / (wa + wb)
	else:
		edge.impulse += impulse
	var correction := delta * (impulse / length)
	state.positions[a] -= correction * wa
	state.positions[b] += correction * wb

func _particle_normals(state: ClothState) -> PackedVector3Array:
	# Local quad tangents approximate the game's corner/normal averaging.
	var result := PackedVector3Array()
	result.resize(state.positions.size())
	for c in state.columns.size():
		var column := state.columns[c]
		var left := state.columns[maxi(c - 1, 0)]
		var right := state.columns[mini(c + 1, state.columns.size() - 1)]
		for row in column.size():
			var along := state.positions[column[mini(row + 1, column.size() - 1)]] - state.positions[column[maxi(row - 1, 0)]]
			var across := state.positions[right[row]] - state.positions[left[row]]
			result[column[row]] = along.cross(across).normalized()
	return result

func simulate(skeleton: Skeleton3D, delta: float) -> void:
	if not skeleton.is_inside_tree():
		return
	# An editor hot reload may retain the previous implementation's state.
	if not _states.is_empty() and not _states[0] is ClothState:
		reset_simulation()
	if _states.is_empty():
		_build(skeleton)
	if _states.is_empty():
		return
	# Bound catch-up and reset on long pauses/teleports instead of exploding.
	if delta > 0.15:
		for state: ClothState in _states:
			_initialize_positions(skeleton, state)
		_accumulator = 0.0
	var dt := maxf(float(_states[0].config.update_delta), 0.001)
	_accumulator = minf(_accumulator + delta, dt * 4.0)
	var frames: Array = []
	for state: ClothState in _states:
		var targets := _animated_positions(skeleton, state)
		if targets[0].distance_to(state.positions[0]) > 0.75:
			_initialize_positions(skeleton, state)
		frames.append({"targets": targets, "obstacles": _world_colliders(skeleton, state, targets)})
	while _accumulator >= dt:
		for s in _states.size():
			var state: ClothState = _states[s]
			var targets: PackedVector3Array = frames[s].targets
			var normals := _particle_normals(state)
			var mass := maxf(float(state.config.mass), 0.0001)
			var acceleration := gravity_acceleration + external_force / mass
			for i in state.positions.size():
				var p: Vector3 = state.positions[i]
				if state.pinned[i]:
					state.positions[i] = targets[i]
				else:
					var velocity := (p - state.previous[i]) / dt
					# Decoded drag: -airDamping * abs(v_hat dot normal) * v.
					var incidence := clampf(absf(velocity.normalized().dot(normals[i])), 0.0, 1.0)
					var directional_drag := lerpf(clampf(tangential_air_ratio, 0.0, 1.0), 1.0, incidence)
					var rate := float(state.config.air_damping) * maxf(air_resistance_scale, 0.0) * directional_drag / mass
					# Exponential integration cannot reverse velocity at large
					# drag settings, and is independent of the render rate.
					var retention := exp(-rate * dt)
					state.positions[i] = p + velocity * retention * dt + acceleration * dt * dt
				state.previous[i] = p
			for edge in state.edges:
				edge.impulse = 0.0
			# Compliant spring pass, followed by cheap hard strain sweeps.
			for edge in state.edges:
				_solve_edge(state, edge, false)
			_collide(state, frames[s].obstacles)
			for iteration in iterations:
				for edge in state.edges:
					if edge.structural:
						_solve_edge(state, edge, true)
				# Resolve contacts less often than distance links; collision
				# projection is the expensive part of the small cloth grid.
				if iteration % 4 == 3:
					_collide(state, frames[s].obstacles)

		_accumulator -= dt
		simulation_steps += 1
	for state: ClothState in _states:
		_apply_pose(skeleton, state)

func _apply_pose(skeleton: Skeleton3D, state: ClothState) -> void:
	if not skeleton.is_inside_tree():
		return
	var skeleton_inverse := skeleton.global_transform.affine_inverse()
	for column in state.columns:
		for row in range(column.size() - 1):
			var i: int = column[row]
			var next: int = column[row + 1]
			var bone: int = state.bones[i]
			var pose := skeleton.get_bone_global_pose(bone)
			var p: Vector3 = skeleton_inverse * state.positions[i]
			var direction: Vector3 = (skeleton_inverse * state.positions[next] - p).normalized()
			if direction.length_squared() > 0.5:
				var original := pose.basis.x.normalized()
				pose.basis = Basis(Quaternion(original, direction)) * pose.basis
			pose.origin = p
			skeleton.set_bone_global_pose(bone, pose)

func constraint_metrics() -> Array:
	var result: Array = []
	for state: ClothState in _states:
		var strain := 0.0
		var extension := 0.0
		for edge in state.edges:
			if edge.structural and edge.length > 0.000001:
				var relative := state.positions[edge.a].distance_to(state.positions[edge.b]) / edge.length - 1.0
				strain = maxf(strain, absf(relative))
				extension = maxf(extension, relative)
		result.append({"name": state.config.name, "particles": state.positions.size(), "max_strain": strain, "max_extension": extension})
	return result
