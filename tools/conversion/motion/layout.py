"""Variable-width transform groups in a decoded motion curve block.

The two leading scalars precede the root's translation and rotation. Every
remaining bone has translation, rotation and scale, in skeleton order. A
group whose first descriptor is 1 occupies just that descriptor (implicit
reference value); otherwise it contains three scalar descriptors. Mode 1
cannot occur inside an explicit group. This grammar consumes every descriptor
on all 4,436 decodable blocks of the local corpus (2026-10-27).

This structural parser deliberately does not select a rotation convention or
interpret the two leading controls. See research/TRANSFORM_LAYOUT_FINDINGS.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from .curves import DecodedCurveBlock, ScalarTrack
from .resource import UnsupportedMotion


@dataclass(frozen=True)
class TransformGroup:
    joint: int
    path: str
    scalar_start: int
    tracks: tuple[ScalarTrack, ...]

    @property
    def implicit(self) -> bool:
        return len(self.tracks) == 1 and self.tracks[0].mode == 1


@dataclass(frozen=True)
class TransformLayout:
    controls: tuple[ScalarTrack, ScalarTrack]
    groups: tuple[TransformGroup, ...]


def decode_transform_layout(decoded: DecodedCurveBlock,
                            bone_count: int) -> TransformLayout:
    """Read the complete group program, rejecting drift and partial layouts."""
    if bone_count < 1 or len(decoded.tracks) < 2:
        raise UnsupportedMotion("missing transform program or skeleton")
    if len(decoded.tracks) != decoded.descriptor_count:
        raise UnsupportedMotion("transform descriptor count mismatch")
    cursor = 2
    groups = []
    for joint in range(bone_count):
        paths = ("translation", "rotation") if joint == 0 else (
            "translation", "rotation", "scale")
        for path in paths:
            if cursor >= len(decoded.tracks):
                raise UnsupportedMotion("truncated transform group")
            count = 1 if decoded.tracks[cursor].mode == 1 else 3
            tracks = tuple(decoded.tracks[cursor:cursor + count])
            if len(tracks) != count or (count == 3 and any(
                    track.mode == 1 for track in tracks)):
                raise UnsupportedMotion("invalid explicit transform group")
            groups.append(TransformGroup(joint, path, cursor, tracks))
            cursor += count
    if cursor != decoded.descriptor_count:
        raise UnsupportedMotion("unconsumed transform descriptors")
    return TransformLayout(tuple(decoded.tracks[:2]), tuple(groups))
