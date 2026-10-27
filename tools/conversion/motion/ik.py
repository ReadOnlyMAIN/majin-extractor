"""Flag-declared two-bone IK, baked into ordinary glTF joint rotations.

The chain graph is binary evidence (3 -> 4 -> flag&8), not bone names.
The solve preserves the incoming FK bend plane and twist. This is a geometric
reconstruction; matching HexaEngine's pole/constraint policy is separate from
the measured endpoint residual, and is reported as such by the exporter.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .resource import UnsupportedMotion
from .transforms import quat_inverse, quat_multiply, quat_rotate


def add(a, b):
    return tuple(x+y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def mul(a, factor):
    return tuple(x*factor for x in a)


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    x, y, z = a
    X, Y, Z = b
    return (y*Z-z*Y, z*X-x*Z, x*Y-y*X)


def length(a):
    return math.sqrt(dot(a, a))


def unit(a):
    norm = length(a)
    if norm < 1e-10:
        raise UnsupportedMotion("degenerate IK direction")
    return mul(a, 1/norm)


def rotation_between(a, b):
    a, b = unit(a), unit(b)
    cosine = max(-1.0, min(1.0, dot(a, b)))
    if cosine < -1 + 1e-10:
        axis = min(((1, 0, 0), (0, 1, 0), (0, 0, 1)), key=lambda v: abs(dot(a, v)))
        return (*unit(cross(a, axis)), 0.0)
    result = (*cross(a, b), 1 + cosine)
    norm = math.sqrt(dot(result, result))
    return mul(result, 1/norm)


@dataclass(frozen=True)
class IKChain:
    start: int
    middle: int
    effector: int
    flags: int


def discover_ik_chains(skeleton) -> tuple[IKChain, ...]:
    chains = []
    for effector, flags in enumerate(skeleton.parent_flags):
        if not flags & 8:
            continue
        middle = skeleton.parent_indices[effector]
        start = skeleton.parent_indices[middle] if middle is not None else None
        if start is None or skeleton.parent_flags[middle] != 4 or \
                skeleton.parent_flags[start] != 3:
            raise UnsupportedMotion("unrecognised flagged IK chain")
        chains.append(IKChain(start, middle, effector, flags))
    if len(chains) != skeleton.chain_count:
        raise UnsupportedMotion("IK chain count does not match skeleton header")
    return tuple(sorted(chains, key=lambda chain: chain.start))


def forward_kinematics(parents, translations, rotations, scales):
    positions, quaternions, global_scales = [], [], []
    for joint, parent in enumerate(parents):
        if parent is None:
            position, rotation, scale = translations[joint], rotations[joint], scales[joint]
        else:
            offset = tuple(a*b for a, b in zip(translations[joint], global_scales[parent]))
            position = add(positions[parent], quat_rotate(quaternions[parent], offset))
            rotation = quat_multiply(quaternions[parent], rotations[joint])
            scale = tuple(a*b for a, b in zip(global_scales[parent], scales[joint]))
        positions.append(position)
        quaternions.append(rotation)
        global_scales.append(scale)
    return positions, quaternions, global_scales


def solve_two_bone(chain, target, parents, translations, rotations, scales):
    """Mutate local upper/middle rotations; return target/clamping diagnostics."""
    positions, world, global_scales = forward_kinematics(
        parents, translations, rotations, scales)
    a, b, c = chain.start, chain.middle, chain.effector
    # Nonuniform ancestor scale introduces shear not represented by this solver.
    indices = {b}
    ancestor = a
    while ancestor is not None:
        indices.add(ancestor)
        ancestor = parents[ancestor]
    for index in indices:
        scale = scales[index]
        if max(scale)-min(scale) > 1e-5 or min(scale) <= 0:
            raise UnsupportedMotion("IK under nonuniform or negative scale is unsupported")
    origin = positions[a]
    upper = sub(positions[b], origin)
    lower = sub(positions[c], positions[b])
    l1, l2 = length(upper), length(lower)
    if min(l1, l2) < 1e-8:
        raise UnsupportedMotion("zero-length IK chain")
    displacement = sub(target, origin)
    distance = length(displacement)
    direction = unit(displacement) if distance > 1e-8 else unit(upper)
    reach = min(l1+l2, max(abs(l1-l2)+1e-8, distance))
    along = (l1*l1 + reach*reach - l2*l2) / (2*reach)
    height = math.sqrt(max(0, l1*l1 - along*along))
    pole = sub(upper, mul(direction, dot(upper, direction)))
    if length(pole) < 1e-8:
        # A straight chain has no unique bend plane; retain a reference frame
        # axis instead of choosing a global up vector that flips at the pole.
        candidates = [quat_rotate(world[a], axis) for axis in ((0, 1, 0), (0, 0, 1))]
        candidate = max(candidates, key=lambda v: length(cross(direction, v)))
        pole = sub(candidate, mul(direction, dot(candidate, direction)))
    elbow = add(origin, add(mul(direction, along), mul(unit(pole), height)))
    endpoint = add(origin, mul(direction, reach))
    upper_delta = rotation_between(upper, sub(elbow, origin))
    upper_world = quat_multiply(upper_delta, world[a])
    parent = parents[a]
    rotations[a] = upper_world if parent is None else quat_multiply(
        quat_inverse(world[parent]), upper_world)
    mid_world = quat_multiply(upper_delta, world[b])
    lower_after_upper = quat_rotate(upper_delta, lower)
    middle_delta = rotation_between(lower_after_upper, sub(endpoint, elbow))
    mid_world = quat_multiply(middle_delta, mid_world)
    rotations[b] = quat_multiply(quat_inverse(upper_world), mid_world)
    solved, _, _ = forward_kinematics(parents, translations, rotations, scales)
    return dict(effector=c, target_error=length(sub(solved[c], target)),
                reachable_error=length(sub(solved[c], endpoint)),
                clamped=abs(distance-reach) > 1e-6)
