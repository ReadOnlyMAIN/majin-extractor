"""Scalar evaluation and quaternion operations for the motion transform program.

Rotations use radians, with column-vector matrix Rz(z) Ry(y) Rx(x).
Noncommuting constant rotations reproduce independent DDM local bind
quaternions; the corpus probe compares all six orders and both angle units.
"""
from __future__ import annotations

from bisect import bisect_right
import math

from .curves import ScalarTrack
from .resource import UnsupportedMotion


SOURCE_FPS = 30.0
IMPLICIT_SCALARS = {0: 0.0, 2: math.pi / 2, 3: math.pi, 4: -math.pi / 2}


def sample_scalar(track: ScalarTrack, frame: float,
                  frame_rate: float = SOURCE_FPS) -> float:
    """Evaluate scalar constants, linear keys and per-second Hermite tangents.

    Descriptor 1 is a whole implicit transform group, never a scalar zero.
    It must be resolved against the reference transform by the caller.
    """
    if track.mode in IMPLICIT_SCALARS:
        return IMPLICIT_SCALARS[track.mode]
    if track.mode == 1:
        raise UnsupportedMotion("implicit transform group is not a scalar")
    if frame_rate <= 0 or not math.isfinite(frame_rate):
        raise ValueError("frame rate must be finite and positive")
    if not track.values:
        raise UnsupportedMotion("scalar payload missing")
    if track.mode == 5:
        return track.values[0]
    right = bisect_right(track.frames, frame)
    if right == 0:
        return track.values[0]
    if right == len(track.frames):
        return track.values[-1]
    left = right - 1
    span = track.frames[right] - track.frames[left]
    fraction = (frame - track.frames[left]) / span
    a, b = track.values[left], track.values[right]
    if track.mode == 6:
        return a + (b - a) * fraction
    if track.mode != 7 or len(track.auxiliaries) != len(track.values):
        raise UnsupportedMotion("invalid tangent curve")
    t = fraction
    seconds = span / frame_rate
    return ((2*t**3 - 3*t*t + 1) * a
            + (t**3 - 2*t*t + t) * seconds * track.auxiliaries[left]
            + (-2*t**3 + 3*t*t) * b
            + (t**3 - t*t) * seconds * track.auxiliaries[right])


def quat_multiply(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (w*X + x*W + y*Z - z*Y,
            w*Y - x*Z + y*W + z*X,
            w*Z + x*Y - y*X + z*W,
            w*W - x*X - y*Y - z*Z)


def quat_inverse(q):
    norm2 = sum(v*v for v in q)
    if not math.isfinite(norm2) or norm2 < 1e-20:
        raise UnsupportedMotion("invalid reference rotation")
    return (-q[0]/norm2, -q[1]/norm2, -q[2]/norm2, q[3]/norm2)


def quat_rotate(q, v):
    return quat_multiply(quat_multiply(q, (*v, 0)), quat_inverse(q))[:3]


def euler_quaternion(angles, order="xyz"):
    """XYZW quaternion; 'xyz' applies X then Y then Z to column vectors."""
    if sorted(order) != ['x', 'y', 'z']:
        raise ValueError("rotation order must be a permutation of xyz")
    result = (0.0, 0.0, 0.0, 1.0)
    for axis in order:
        half = angles['xyz'.index(axis)] * 0.5
        q = [0.0, 0.0, 0.0, math.cos(half)]
        q['xyz'.index(axis)] = math.sin(half)
        result = quat_multiply(q, result)
    return result


def local_reference_rotations(skeleton):
    """Motion translations are local, but its bind quaternions are global."""
    return [
        rotation if parent is None else quat_multiply(
            quat_inverse(skeleton.rotations[parent]), rotation)
        for parent, rotation in zip(skeleton.parent_indices, skeleton.rotations)
    ]
