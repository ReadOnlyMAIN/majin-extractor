"""motionPackage (bare-body resource): skeleton, curves and timeline.

Verified structure (REVERSE_MOTION.md):

- the body starts at ``+0x80`` with
  ``[u32 block_count][u32 offset_0] ... [u32 offset_count]`` where the
  offsets are *relative to 0x80* and ``offset_count == file size - 0x80``
  (verified on every shipped file);
- ``block 0`` is the motion skeleton (bone ids in DDM transform order,
  parent/flag pairs, and the exact DDM bind pose);
- blocks after the skeleton are curve blocks with a 4-byte head
  ``[type][A][0][B]``;
- the penultimate block is the timeline index: ``[u32 segment_count]`` then
  ``segment_count + 1`` frame boundaries then ``segment_count`` pairs of
  ``(value, 0xFFFFFFFF)``;
- the last block is a short constant end-of-list marker (~16 bytes).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .resource import (
    HEADER_SIZE,
    ResourceHeader,
    UnsupportedMotion,
    read_header,
)

PACKAGE_MAGIC = b"\x00\x00\x00\x00"
END_OF_LIST = 0xFFFFFFFF


def align4(offset: int) -> int:
    return (offset + 3) & ~3


@dataclass
class MotionSkeleton:
    """Skeleton block of a motionPackage (bind pose in DDM transform order)."""

    bone_count: int
    chain_count: int                    # candidate IK chain count
    parent_indices: list[int]           # 0-based parent transform index
    parent_flags: list[int]             # per-bone flag byte
    bone_ids: list[int]                 # global ids, DDM transform order
    translations: list[tuple[float, float, float]]
    rotations: list[tuple[float, float, float, float]]


@dataclass
class CurveBlock:
    """A curve block: header bytes kept raw plus its body for decoding."""

    slot_index: int                     # table slot owning this block
    block_index: int                    # index into the unique curve blocks
    offset: int                         # absolute file offset
    header: bytes                       # 4 bytes [type][A][0][B]
    body: bytes                         # payload after the header


@dataclass
class TimelineBlock:
    """Timeline index slicing the package into per-segment frame ranges."""

    segment_count: int
    boundaries: list[int]               # segment_count + 1 frame numbers
    pairs: list[tuple[int, int]]        # (value, sentinel) per segment


@dataclass
class MotionPackage:
    """Parsed motionPackage body: skeleton, curve blocks and timeline."""

    header: ResourceHeader
    slots: list[Slice] = field(default_factory=list)
    curve_blocks: list[CurveBlock] = field(default_factory=list)
    skeleton: MotionSkeleton | None = None
    timeline: TimelineBlock | None = None
    footer: bytes = b""

    @property
    def has_curves(self) -> bool:
        return bool(self.curve_blocks)


@dataclass
class Slice:
    """One table slot: absolute byte range of the resource body."""

    slot_index: int
    start: int
    stop: int

    @property
    def size(self) -> int:
        return self.stop - self.start


def parse_skeleton(block: bytes) -> MotionSkeleton | None:
    """Decode the skeleton block when the verified layout fits exactly.

    Layout: ``[u8 bone_count][u8 chain_count][u8 0][u8 0]`` then
    ``(bone_count - 2)`` ``(parent, flag)`` byte pairs, then ``bone_count``
    bone-id bytes, padding to a 4-byte boundary, then ``bone_count`` bind
    entries of ``[translation 3*f32][rotation 4*f32]`` (big-endian).
    """
    if len(block) < 8:
        return None
    bone_count = block[0]
    chain_count = block[1]
    if block[2] != 0 or block[3] != 0 or not 1 <= bone_count <= 1023:
        return None
    body_size = 4 + max(0, bone_count - 2) * 2 + bone_count
    floats_start = align4(body_size)
    if floats_start + bone_count * 28 != len(block):
        return None  # a different block layout; never guess
    cursor = 4
    parent_indices: list[int] = []
    parent_flags: list[int] = []
    for _ in range(max(0, bone_count - 2)):
        parent, flag = block[cursor], block[cursor + 1]
        if parent >= bone_count:
            return None
        parent_indices.append(parent)
        parent_flags.append(flag)
        cursor += 2
    ids = list(block[cursor:cursor + bone_count])
    if len(set(ids)) != bone_count:
        return None
    translations = []
    rotations = []
    for index in range(bone_count):
        base = floats_start + index * 28
        translation = struct.unpack_from(">3f", block, base)
        rotation = struct.unpack_from(">4f", block, base + 12)
        norm = sum(c * c for c in rotation) ** 0.5
        if not 0.98 <= norm <= 1.02:
            return None
        translations.append(translation)
        rotations.append(tuple(c / norm for c in rotation))
    return MotionSkeleton(
        bone_count=bone_count,
        chain_count=chain_count,
        parent_indices=parent_indices,
        parent_flags=parent_flags,
        bone_ids=ids,
        translations=translations,
        rotations=rotations,
    )


def parse_timeline(block: bytes) -> TimelineBlock | None:
    """Decode the timeline index when the verified layout fits exactly.

    ``segment_count`` followed by ``segment_count + 1`` strictly increasing
    frame boundaries, then every remaining aligned ``(value, flag)`` pair —
    the pair count is NOT assumed to equal ``segment_count`` (chr370 stores
    41 pairs for 38 segments; the pairing is an open Phase 2.4 question).
    """
    if len(block) < 12:
        return None
    segment_count = struct.unpack_from(">I", block, 0)[0]
    if not 1 <= segment_count <= 200_000:
        return None
    cursor = 4
    boundaries = [
        struct.unpack_from(">I", block, cursor + 4 * i)[0]
        for i in range(segment_count + 1)
    ]
    if any(b <= a for a, b in zip(boundaries, boundaries[1:])):
        return None
    cursor += (segment_count + 1) * 4
    remaining = len(block) - cursor
    if remaining < 0 or remaining % 8 != 0:
        return None
    pairs = [
        (struct.unpack_from(">I", block, cursor + 8 * i)[0],
         struct.unpack_from(">I", block, cursor + 8 * i + 4)[0])
        for i in range(remaining // 8)
    ]
    if any(sentinel != END_OF_LIST for _value, sentinel in pairs[:4]) \
            and any(pair[1] not in (0xFFFFFFFF, 0) for pair in pairs):
        return None
    return TimelineBlock(
        segment_count=segment_count, boundaries=boundaries, pairs=pairs
    )


def parse_motion_package(path: Path) -> MotionPackage:
    """Parse a motionPackage resource and classify its blocks."""
    path = Path(path)
    data = path.read_bytes()
    header = read_header(data, expected_magic=b"\x00\x00\x00\x00",
                         source="motionPackage")
    slot_count = struct.unpack_from(">I", data, 0x80)[0]
    if slot_count < 1:
        raise UnsupportedMotion(f"motionPackage {path.name}: empty block table.")
    if HEADER_SIZE + 4 + (slot_count + 1) * 4 > len(data):
        raise UnsupportedMotion(f"motionPackage {path.name}: block table truncated.")
    raw_offsets = struct.unpack_from(">" + "I" * (slot_count + 1), data, 0x84)
    if raw_offsets[-1] != len(data) - HEADER_SIZE:
        raise UnsupportedMotion(
            f"motionPackage {path.name}: offset table inconsistent with file size."
        )
    absolute = [HEADER_SIZE + offset for offset in raw_offsets]

    package = MotionPackage(header=header)
    printed_curves = 0
    for slot_index in range(slot_count):
        start, stop = absolute[slot_index], absolute[slot_index + 1]
        package.slots.append(Slice(slot_index=slot_index, start=start, stop=stop))
        payload = data[start:stop]
        if not payload:
            continue
        decoded = parse_skeleton(payload)
        if decoded is not None and package.skeleton is None:
            package.skeleton = decoded
            continue
        decoded_timeline = parse_timeline(payload)
        if decoded_timeline is not None and package.timeline is None:
            package.timeline = decoded_timeline
            continue
        if slot_index == slot_count - 1:
            package.footer = payload[-16:]
            break
        package.curve_blocks.append(CurveBlock(
            slot_index=slot_index,
            block_index=printed_curves,
            offset=start,
            header=payload[:4],
            body=payload[4:],
        ))
        printed_curves += 1
    if package.skeleton is None:
        raise UnsupportedMotion(
            f"motionPackage {path.name}: no recognisable skeleton block."
        )
    if package.timeline is None:
        # Some tiny packages (empty placeholder assets) carry no timeline.
        pass
    return package

