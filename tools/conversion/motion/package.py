"""motionPackage (bare-body resource): skeleton, curves and curve index.

Verified structure (REVERSE_MOTION.md):

- the body starts at ``+0x80`` with
  ``[u32 block_count][u32 offset_0] ... [u32 offset_count]`` where the
  offsets are *relative to 0x80* and ``offset_count == file size - 0x80``
  (verified on every shipped file);
- ``block 0`` is the motion skeleton (bone ids in DDM transform order,
  parent/flag pairs, and the exact DDM bind pose);
- blocks after the skeleton are curve blocks with a 4-byte head containing
  two big-endian u16 values: a 3-bit descriptor count and a sample-count-like
  value;
- the penultimate block is a curve-slot index: ``[u32 slot_count]``, then
  ``slot_count + 1`` byte offsets into this same block, then u32 words grouped
  by those offsets.
  These offsets were previously mistaken for frame boundaries;
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
    header: bytes                       # 4 bytes: [u16 descriptor_count][u16 sample_count]
    body: bytes                         # payload after the header


@dataclass
class CurveIndexBlock:
    """Index for logical curve slots; entry semantics are not decoded yet."""

    slot_count: int
    entry_offsets: list[int]            # offsets relative to this block
    words: list[int]                     # raw grouped u32 payload

    @property
    def groups(self) -> list[list[int]]:
        """Return the raw u32 words belonging to each logical curve slot."""
        start = self.entry_offsets[0]
        return [
            self.words[(left - start) // 4:(right - start) // 4]
            for left, right in zip(self.entry_offsets, self.entry_offsets[1:])
        ]

    # Compatibility names for callers written before the offset proof.  They
    # deliberately carry no temporal terminology in the maintained API.
    @property
    def segment_count(self) -> int:
        return self.slot_count

    @property
    def boundaries(self) -> list[int]:
        return self.entry_offsets

    @property
    def pairs(self) -> list[tuple[int, int]]:
        """Deprecated compatibility view; grouping is not universally pairs."""
        return list(zip(self.words[::2], self.words[1::2]))


TimelineBlock = CurveIndexBlock


@dataclass
class RigControlBlock:
    """Counted variable-length control records preceding the package marker."""

    records: list[bytes]
    marker: bytes


@dataclass
class MotionPackage:
    """Parsed motionPackage body: skeleton, curve blocks and curve index."""

    header: ResourceHeader
    slots: list[Slice] = field(default_factory=list)
    curve_blocks: list[CurveBlock] = field(default_factory=list)
    skeleton: MotionSkeleton | None = None
    curve_index: CurveIndexBlock | None = None
    footer: bytes = b""
    rig_controls: RigControlBlock | None = None

    @property
    def has_curves(self) -> bool:
        return bool(self.curve_blocks)

    @property
    def timeline(self) -> CurveIndexBlock | None:
        """Deprecated compatibility alias; the block is not a timeline."""
        return self.curve_index


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


def parse_curve_index(block: bytes) -> CurveIndexBlock | None:
    """Decode the penultimate curve-slot index.

    The first offset is exactly the end of the offset table and the final
    offset is exactly ``len(block)``.  Consecutive offsets delimit groups of
    u32 words, so their spacing is structural and not a frame grid. Payload
    word semantics remain open research.
    """
    if len(block) < 12:
        return None
    slot_count = struct.unpack_from(">I", block, 0)[0]
    if not 1 <= slot_count <= 200_000:
        return None
    cursor = 4
    entry_offsets = [
        struct.unpack_from(">I", block, cursor + 4 * i)[0]
        for i in range(slot_count + 1)
    ]
    table_end = 4 + (slot_count + 1) * 4
    if entry_offsets[0] != table_end or entry_offsets[-1] != len(block):
        return None
    if any(b < a for a, b in zip(entry_offsets, entry_offsets[1:])):
        return None
    if any((offset - table_end) % 4 for offset in entry_offsets):
        return None
    words = [
        struct.unpack_from(">I", block, table_end + 4 * i)[0]
        for i in range((len(block) - table_end) // 4)
    ]
    return CurveIndexBlock(
        slot_count=slot_count, entry_offsets=entry_offsets, words=words
    )


parse_timeline = parse_curve_index


def parse_rig_controls(block: bytes) -> RigControlBlock | None:
    """Split the final block into 0xFF-terminated controls and marker bytes.

    The record payload semantics are intentionally left raw.  A zero count
    is the common no-controls form used by gimmicks and simpler characters.
    """
    if not block:
        return None
    count = block[0]
    cursor = 1
    records = []
    for _ in range(count):
        end = block.find(b"\xFF", cursor)
        if end < cursor:
            return None
        records.append(block[cursor:end])
        cursor = end + 1
    marker = block[cursor:]
    if len(marker) < 8:
        return None
    return RigControlBlock(records=records, marker=marker)


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
        if slot_index == slot_count - 2:
            decoded_index = parse_curve_index(payload)
            if decoded_index is not None:
                package.curve_index = decoded_index
                continue
        if slot_index == slot_count - 1:
            package.footer = payload
            package.rig_controls = parse_rig_controls(payload)
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
    if package.curve_index is None:
        # Some tiny packages (empty placeholder assets) carry no curve index.
        pass
    return package
