@tool
extends SkeletonModifier3D
## CSR capsules can join two different animated bones. Update their endpoints
## after pose transfer/IK and before the spring simulator samples collisions.

@export var simulator_path := NodePath("../MajinSecondaryMotion")

func _process_modification() -> void:
	var skeleton := get_skeleton()
	var simulator := get_node_or_null(simulator_path)
	if skeleton != null and simulator != null:
		update_collisions(skeleton, simulator)

func update_collisions(skeleton: Skeleton3D, simulator: Node) -> void:
	for collider in simulator.get_children():
		if not collider.has_meta("majin_csr_endpoints"):
			continue
		var entry: Dictionary = collider.get_meta("majin_csr_endpoints")
		var bone_a := skeleton.find_bone(entry["bone_a"])
		var bone_b := skeleton.find_bone(entry["bone_b"])
		if bone_a < 0 or bone_b < 0:
			continue
		var p: Array = entry["point_a"]
		var q: Array = entry["point_b"]
		var a := skeleton.get_bone_global_pose(bone_a) * Vector3(p[0], p[1], p[2])
		var b := skeleton.get_bone_global_pose(bone_b) * Vector3(q[0], q[1], q[2])
		var axis := b - a
		var length := axis.length()
		var radius := float(entry["radius"])
		collider.position = (a + b) * 0.5
		collider.quaternion = Quaternion(Vector3.UP, axis / length) if length > 0.000001 else Quaternion.IDENTITY
		collider.set("radius", radius)
		collider.set("height", length + 2.0 * radius)
