"""Prototype curve-block decoding (Phase 2 research, REVERSE_MOTION.md).

Model confirmed on gim103 / chr370 / chr540 / chr980 data:

- Curve blocks start with a 4-byte header ``[type][A][0][B]``. A is a
  per-block frame/key width, B a channel-phase selector (both formalisms
  are still being refined; values are kept raw).
- Each block carries a **bit-packed channel table** at its start, followed
  by **value sections made of ``(f32 canonical value, u32 0)`` pairs**.
  A pair whose value is a bone translation magnitude (~100-300 units) or
  a unit-length quaternion component tracks real pose constants.
- A trailing ``B == 2`` block with a 16-bit channel table is a
  **constant-only curve**: exactly one ``(f32 value, 0)`` pair follows
  (e.g. gim103 blocks 3/4 both decode ``0.842059`` with the same table
  differing in a single bit — the studied A/B discriminator).

This module exposes only what survives all probes; the layout-place
decoder for the bit-packed streams is still Phase 2 research.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .package import CurveBlock


@dataclass
class CurveSection:
    """Declared layout of one curve block's body (research view)."""

    channel_words: int          # u32 words consumed by the bit-packed table
    constants: list[tuple[float, int]]  # decoded (value, sentinel) pairs
    trailing_words: int         # words after the last declared pair


def _is_canonical_f32(word: int) -> bool:
    value = struct.unpack('>f', struct.pack('>I', word))[0]
    return value == value and abs(value) < 1e6


def decode_constant_section(body: bytes, channel_words: int,
                            offset: int) -> CurveSection:
    """Decode a run of well-aligned ``(value, 0)`` constant pairs."""
    constants = []
    cursor = channel_words * 4 + offset
    while cursor + 8 <= len(body):
        value_raw, sentinel_raw = struct.unpack_from('>II', body, cursor)
        if sentinel_raw != 0:
            break
        constants.append((struct.unpack('>f', struct.pack('>I', value_raw))[0],
                          sentinel_raw))
        cursor += 8
    trail = (len(body) - cursor) // 4
    return CurveSection(
        channel_words=channel_words,
        constants=constants,
        trailing_words=trail,
    )


def locate_value_start(body: bytes, default_channel_words: int) -> int:
    """Return the offset (in bytes) after the bit-packed channel table.

    Verified landmark: the first canonical ``(value, 0x00000000)`` pair
    boundary at a 4-byte alignment; the rest of the tables are
    reconstructed from this anchor.
    """
    run = 0
    candidate = default_channel_words * 4
    for off in range(0, len(body) - 8, 4):
        value_raw, sentinel_raw = struct.unpack_from('>II', body, off)
        key = _is_canonical_f32(value_raw)
        run = run + 1 if key and sentinel_raw == 0 else 0
        if run >= 3:
            return max(0, off - 2 * 8)
    return candidate


def block_curve_header(block: CurveBlock) -> tuple[int, int, int, int]:
    """Return ``(type, A, phase, B)`` of a curve block.

    Corpus-wide, byte 2 is almost always 0 (4415/4474) but is occasionally
    1 (59 blocks) — kept as a raw ``phase`` byte rather than asserted zero.
    """
    if len(block.header) != 4:
        raise ValueError('curve block header must be 4 bytes')
    kind, first, phase, second = block.header
    return kind, first, phase, second


def bind_pose_hits(package, tolerance: float = 1e-3) -> tuple[int, int]:
    """Count ``(value, 0)`` curve constants matching a bind-pose component.

    Oracle between the curve blocks and the verified motion skeleton:
    most frozen channel values of a clip equal some bone's bind
    translation/quat component (proven byte-close on gim103 bone id 9,
    translation ``(-1.2651, -5.6713, 229.172)``). Returns ``(hits, total)``.
    """
    if package.skeleton is None:
        return 0, 0
    components: list[float] = []
    for trio in package.skeleton.translations:
        components.extend(trio)
    for quat in package.skeleton.rotations:
        components.extend(quat)
    hits = 0
    total = 0
    for block in package.curve_blocks:
        if is_constant_curve(block):
            continue
        values, ok = _block_pose_constants(block, components, tolerance)
        hits += ok
        total += values
    return hits, total


def _block_pose_constants(block: CurveBlock, components: list[float],
                          tolerance: float) -> tuple[int, int]:
    """Decode value-section constants of one block; (total, matches)."""
    kind, _A, _phase, _B = block_curve_header(block)
    if kind in (3, 4):
        return 0, 0  # rare large blocks, not validated
    start_words = locate_value_start(block.body, 10)//4
    constants = decode_constant_section(
        block.body, start_words, 0).constants
    total = 0
    matched = 0
    for value in (v for v, _s in constants):
        total += 1
        nearest = abs(value - components[0])
        for reference in components:
            if abs(value - reference) < nearest:
                nearest = abs(value - reference)
        if nearest <= tolerance:
            matched += 1
    return total, matched


def is_constant_curve(block: CurveBlock) -> bool:
    """True when the block is a B==2 constant-only curve (verified form)."""
    kind, _A, _zero, second = block_curve_header(block)
    return kind == 0 and second == 2 and len(block.body) == 20


def constant_curve_value(block: CurveBlock) -> float | None:
    """Decoded float of a constant-only curve, else ``None``."""
    kind, _A, _zero, second = block_curve_header(block)
    if not (kind == 0 and second == 2 and len(block.body) == 20):
        return None
    return struct.unpack('>f', block.body[-4:])[0]