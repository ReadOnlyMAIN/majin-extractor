"""HexaEngine motion resource decoding for skinned DDM assets.

The animation data of a character or gimmick lives next to its DDM in the
unpacked KB tree:

- ``KB/motionSequence/<name>/<name>`` (magic ``psmr``) indexes the named
  animation clips (state records) of the character.
- ``KB/motionPackage/<name>/BigEndian/<name>`` holds the skeleton block,
  the compressed curve blocks and a timeline index that slices the PACKAGE
  per time segment.

Both resources share the 0x80-byte resource header of the other KB formats
(``_resource_header`` in :mod:`.resource`). This package replaces the old
experimental ``motion_decode`` prototype with a clean, generic decoder.
"""
from __future__ import annotations

from .resource import ResourceHeader, UnsupportedMotion
from .sequence import SequenceRecord, parse_motion_sequence
from .package import (
    CurveBlock,
    MotionPackage,
    MotionSkeleton,
    TimelineBlock,
    parse_motion_package,
)

__all__ = [
    "CurveBlock",
    "MotionPackage",
    "MotionSkeleton",
    "ResourceHeader",
    "SequenceRecord",
    "TimelineBlock",
    "UnsupportedMotion",
    "parse_motion_package",
    "parse_motion_sequence",
]
