"""Curve-block decoding primitives (Phase 2 research, REVERSE_MOTION.md).

Model confirmed on gim103 / chr300 / chr370 / chr540 data:

- Curve blocks start with two big-endian u16 values.  The first is the exact
  number of 3-bit descriptors at the start of the body. Eight descriptors are
  read least-significant-first inside each big-endian 24-bit word. Treating
  its high byte as a curve ``type`` was wrong (large rigs simply have more
  than 255 descriptors).  The second value is sample-count-like and equals
  the qstm duration candidate + 1 for most mappable blocks.
- Lossless scalar storage per descriptor: mode 5 = constant (1 f32),
  mode 6 = dated keys (u8/u16 counts and times), mode 7 = keys plus a
  per-second tangent (cubic Hermite); modes 0-4 store no payload.
- Blocks are animation *segments*: consecutive blocks chain through the
  carry oracle (:func:`carry_fidelity`, :func:`carry_chains`) — a
  payload-free channel of a following block holds the value the previous
  segment ended with.
- One scalar = one transform component, grouped in 7-scalar-per-bone runs
  anchored per package (:func:`resolve_scalar_channel_base`). The mapping
  carries *translations* (verified against the motion bind pose) but its
  rotation reading is refuted: the Phase 2.2 probes measured quaternion
  norms 38..2002 on chr540 and zero unit quaternions corpus-wide, so
  :func:`decode_scalar_clip` gates every emitted channel on a pose oracle
  and refuses blocks that would fabricate poses.

This module exposes only what survives probes; the humanoid channel
program (root prefix + euler triplets, REVERSE_MOTION.md 2.2) is still
Phase 2 research — see research/humanoid_layout_probe.py.
"""
from __future__ import annotations

import struct
import math
from collections import defaultdict
from dataclasses import dataclass

from .package import CurveBlock
from .resource import UnsupportedMotion


@dataclass
class CurveSection:
    """Declared layout of one curve block's body (research view)."""

    channel_words: int          # u32 words consumed by the bit-packed table
    constants: list[tuple[float, int]]  # decoded (value, sentinel) pairs
    trailing_words: int         # words after the last declared pair


@dataclass
class ScalarTrack:
    """One structurally decoded scalar track; transform semantics are open."""

    scalar_index: int
    mode: int
    frames: list[int]
    values: list[float]
    auxiliaries: list[float]


@dataclass
class DecodedCurveBlock:
    """Lossless scalar-storage view of a curve block."""

    descriptor_count: int
    sample_count: int
    tracks: list[ScalarTrack]
    descriptor_padding: bytes
    key_table_padding: bytes
    payload_offset: int
    key_time_width: int


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
    """Return the four raw header bytes (legacy research helper)."""
    if len(block.header) != 4:
        raise ValueError('curve block header must be 4 bytes')
    kind, first, phase, second = block.header
    return kind, first, phase, second


def curve_header_counts(block: CurveBlock) -> tuple[int, int]:
    """Return ``(descriptor_count, sample_count)`` from the BE u16 header."""
    if len(block.header) != 4:
        raise ValueError('curve block header must be 4 bytes')
    return struct.unpack('>HH', block.header)


def descriptor_codes(block: CurveBlock) -> list[int]:
    """Decode the leading 3-bit descriptor stream.

    Eight descriptors occupy each big-endian 24-bit word, from its least
    significant 3 bits upwards. The final word is padded with zero modes.
    This orientation is discriminated corpus-wide by descriptor padding,
    strictly increasing interior key times and exact payload consumption.
    """
    count, _sample_count = curve_header_counts(block)
    table_size = ((count + 7) // 8) * 3
    if table_size > len(block.body):
        raise ValueError('curve descriptor stream exceeds block body')
    codes = []
    for offset in range(0, table_size, 3):
        packed = int.from_bytes(block.body[offset:offset + 3], "big")
        codes.extend((packed >> (3 * index)) & 7 for index in range(8))
    if any(codes[count:]):
        raise UnsupportedMotion("nonzero curve descriptor padding")
    return codes[:count]


def _decode_with_key_width(block: CurveBlock, key_time_width: int) \
        -> DecodedCurveBlock:
    descriptor_count, sample_count = curve_header_counts(block)
    modes = descriptor_codes(block)
    cursor = ((descriptor_count + 7) // 8) * 3
    aligned_cursor = (cursor + key_time_width - 1) & -key_time_width
    descriptor_padding = block.body[cursor:aligned_cursor]
    cursor = aligned_cursor
    key_frames: dict[int, list[int]] = {}
    count_width = key_time_width
    for scalar_index, mode in enumerate(modes):
        if mode not in (6, 7):
            continue
        if cursor + count_width > len(block.body):
            raise UnsupportedMotion("truncated curve key count")
        interior_count = int.from_bytes(
            block.body[cursor:cursor + count_width], "big"
        )
        cursor += count_width
        byte_count = interior_count * key_time_width
        if cursor + byte_count > len(block.body):
            raise UnsupportedMotion("truncated curve key times")
        interior = [
            int.from_bytes(
                block.body[offset:offset + key_time_width], "big"
            )
            for offset in range(cursor, cursor + byte_count, key_time_width)
        ]
        cursor += byte_count
        if interior != sorted(set(interior)) or any(
                not 0 < frame < sample_count - 1 for frame in interior):
            raise UnsupportedMotion("invalid curve key times")
        key_frames[scalar_index] = [0, *interior, sample_count - 1]

    payload_offset = (cursor + 3) & ~3
    if payload_offset > len(block.body):
        raise UnsupportedMotion("invalid curve payload alignment")
    padding = block.body[cursor:payload_offset]
    cursor = payload_offset
    tracks = []
    for scalar_index, mode in enumerate(modes):
        frames = key_frames.get(scalar_index, [0])
        word_count = (
            1 if mode == 5 else
            len(frames) if mode == 6 else
            2 * len(frames) if mode == 7 else 0
        )
        byte_count = word_count * 4
        if cursor + byte_count > len(block.body):
            raise UnsupportedMotion("truncated curve float payload")
        words = list(struct.unpack_from(">" + "f" * word_count,
                                        block.body, cursor))
        if not all(math.isfinite(value) for value in words):
            raise UnsupportedMotion("non-finite curve float payload")
        cursor += byte_count
        values = words[::2] if mode == 7 else words
        auxiliaries = words[1::2] if mode == 7 else []
        tracks.append(ScalarTrack(
            scalar_index=scalar_index,
            mode=mode,
            frames=frames if word_count else [],
            values=values,
            auxiliaries=auxiliaries,
        ))
    if cursor != len(block.body):
        raise UnsupportedMotion(
            f"unaccounted curve payload bytes: {len(block.body) - cursor}"
        )
    return DecodedCurveBlock(
        descriptor_count=descriptor_count,
        sample_count=sample_count,
        tracks=tracks,
        descriptor_padding=descriptor_padding,
        key_table_padding=padding,
        payload_offset=payload_offset,
        key_time_width=key_time_width,
    )


def decode_curve_block(block: CurveBlock) -> DecodedCurveBlock:
    """Decode the verified scalar storage without assigning bone semantics.

    Clips through 256 samples use u8 key counts/times. Longer clips normally
    use u16 counts/times. A small exceptional family has a different wide-key
    layout and is rejected rather than guessed.
    """
    descriptor_count, sample_count = curve_header_counts(block)
    if descriptor_count == 0 or sample_count < 2:
        raise UnsupportedMotion("placeholder or invalid curve block")
    widths = (1,) if sample_count <= 256 else (2,)
    errors = []
    for width in widths:
        try:
            return _decode_with_key_width(block, width)
        except (UnsupportedMotion, struct.error) as exc:
            errors.append(str(exc))
    raise UnsupportedMotion("; ".join(errors))


BASE_CONSENSUS = 0.8


def resolve_scalar_channel_base(package) -> int | None:
    """Derive the per-package 7-scalar-per-bone channel base from evidence.

    Proven layout (REVERSE_MOTION.md 2.2): every bone owns 7 consecutive
    scalar indices ``T.x,T.y,T.z,R.x,R.y,R.z,R.w`` so that
    ``bone = (scalar + base) // 7``. The base is anchored on curve
    constants (mode 5) that byte-equal exactly ONE non-zero bind component
    (identities shared by several bones or border components are excluded
    as ambiguous). Returns the winning base when at least two anchors vote
    for it, every animated scalar then stays inside the rig, and the vote
    is near-unanimous (:data:`BASE_CONSENSUS` of all anchors) — else
    ``None`` — no guessed mapping is produced.

    The consensus gate is measured, not cosmetic (Phase 2.2 probes):
    gim103 votes 5/5 for base 21, while chr540's 57 anchors split across
    bases 205/181/317/… (top share 0.28) because its blocks cover several
    channel sections with different offsets — a single base cannot describe
    them, and exporting through the plurality winner fabricated garbage
    rotations (quaternion norms 38..2002).

    Scope note: the anchor evidence covers the *translation* components;
    the same 7-scalar reading applied to the rotation components yields
    non-unit quaternions, which is why :func:`decode_scalar_clip` gates
    rotations on a unit-length pose oracle instead of trusting this base
    blindly.
    """
    skeleton = package.skeleton
    if skeleton is None or not package.curve_blocks:
        return None
    owners: dict[bytes, list] = {}
    for bone, (trio, quat) in enumerate(
            zip(skeleton.translations, skeleton.rotations)):
        for comp, value in enumerate((*trio, *quat)):
            owners.setdefault(struct.pack(">f", value), []).append(
                (bone, comp))
    decoded = []
    for block in package.curve_blocks:
        try:
            decoded.append(decode_curve_block(block))
        except (UnsupportedMotion, ValueError):
            return None
    votes: dict[int, int] = {}
    total_anchors = 0
    for block_decoded in decoded:
        for track in block_decoded.tracks:
            if not track.values or track.values[0] != track.values[-1]:
                continue
            candidates = owners.get(struct.pack(">f", track.values[0]), [])
            if len(candidates) == 1:
                bone, comp = candidates[0]
                key = 7 * bone + comp - track.scalar_index
                votes[key] = votes.get(key, 0) + 1
                total_anchors += 1
    if not total_anchors:
        return None
    for base, count in sorted(votes.items(), key=lambda item: -item[1]):
        if count < 2 or count < BASE_CONSENSUS * total_anchors:
            continue
        bones = skeleton.bone_count
        in_rig = True
        for block_decoded in decoded:
            for track in block_decoded.tracks:
                if track.values and not (
                        0 <= (track.scalar_index + base) // 7 < bones):
                    in_rig = False
        if in_rig:
            return base
    return None


QUAT_NORM_TOLERANCE = 1e-3


def decode_scalar_clip(package, block: CurveBlock, base: int,
                       frame_rate: float = 60.0) -> dict | None:
    """Build a partial glTF clip dict from one verified curve block.

    Returns ``None`` (never a guessed clip) when the block's channel layout
    does not resolve strictly inside the package rig. Mapping (proven,
    REVERSE_MOTION.md 2.2/2.3): one scalar = one component, grouped in
    7-scalar-per-bone runs with the package channel base; mode 5 = the
    constant channel key, modes 6/7 = dated keys (u8/u16 frames).
    A bone contributes a translation channel only when its three
    translation scalars share one strictly-increasing grid, a rotation
    channel only when its four quaternion scalars do; everything else is
    skipped conservatively (no reconstructed pose).

    Pose oracles (Phase 2.2 empirical, REVERSE_MOTION.md): a rotation
    channel is accepted only when every emitted quaternion is unit-length
    within :data:`QUAT_NORM_TOLERANCE` (the stored components are emitted
    normalised). An unmet oracle cancels the whole block — the Phase 2.2
    probes measured chr540 rotation channels built this way at quaternion
    norms 38..2002, i.e. the 7-scalar mapping does not carry rotations,
    and a guessed pose is worse than no clip. Translation channels carry
    no per-block oracle: mid-chain segments legitimately hold carried
    values that differ from the bind pose (verified on gim103 blocks 1/2/6/7).
    """
    decoded = decode_curve_block(block)
    bones = package.skeleton.bone_count
    grids = defaultdict(dict)
    for track in decoded.tracks:
        if not track.values:
            continue
        bone, comp = divmod(track.scalar_index + base, 7)
        if not 0 <= bone < bones:
            return None  # base drift: no guessed channels
        frames = track.frames if track.frames else [0]
        if len(frames) != len(track.values):
            return None
        if any(b <= a for a, b in zip(frames, frames[1:])):
            return None
        grids[(bone, comp >= 3)][comp] = (frames, track.values)
    channels = []
    for (bone, is_rotation), comps in grids.items():
        wanted = (3, 4, 5, 6) if is_rotation else (0, 1, 2)
        if set(comps) != set(wanted):
            continue  # incomplete component set: skipped, no reconstruction
        union = sorted({frame for comp in wanted
                        for frame in comps[comp][0]})
        values = []
        for frame in union:
            pose = []
            for comp in wanted:
                frames, track_values = comps[comp]
                current = track_values[0]  # hold first key before its time
                for key_frame, value in zip(frames, track_values):
                    if key_frame > frame:
                        break
                    current = value
                pose.append(current)
            values.append(pose)
        if is_rotation:
            for pose in values:
                norm = math.sqrt(sum(c * c for c in pose))
                if abs(norm - 1.0) > QUAT_NORM_TOLERANCE:
                    return None  # not a quaternion: mapping refused
            values = [
                [component / norm for component in pose]
                for pose, norm in (
                    (pose, math.sqrt(sum(c * c for c in pose)))
                    for pose in values)
            ]
        times = [frame / frame_rate for frame in union]
        path = "rotation" if is_rotation else "translation"
        channels.append({
            "joint": bone, "path": path,
            "times": times, "values": values,
            "interpolation": "STEP",
        })
    if not channels:
        return None
    return {
        "channels": channels,
        "sample_count": decoded.sample_count,
        "resampled": "step_hold_union",
        "frame_rate": frame_rate,
    }


CARRY_TOLERANCE = 1e-4
CHAIN_CARRY_THRESHOLD = 0.5


def block_end_values(decoded) -> dict[int, float]:
    """Last emitted value of every valued track of a decoded block."""
    return {
        track.scalar_index: track.values[-1]
        for track in decoded.tracks if track.values
    }


def block_constants(decoded) -> dict[int, float]:
    """Constant (mode 5) value of every constant track of a decoded block."""
    return {
        track.scalar_index: track.values[0]
        for track in decoded.tracks
        if track.mode == 5 and track.values
    }


def carry_fidelity(previous, following) -> tuple[int, int]:
    """Measure the segment-chain oracle between two consecutive blocks.

    Verified structure (Phase 2.4, REVERSE_MOTION.md): curve blocks are
    animation *segments* — a following block whose channel was left
    payload-free (modes 0-4) holds the value the previous segment ended
    with, and whenever that channel is stored as a mode-5 constant the two
    floats agree byte-close. Returns ``(hits, comparisons)`` over the
    scalars that are constants in ``following`` and valued in ``previous``.
    """
    end_values = block_end_values(previous)
    constants = block_constants(following)
    hits = 0
    comparisons = 0
    for scalar, value in constants.items():
        previous_value = end_values.get(scalar)
        if previous_value is None:
            continue
        comparisons += 1
        if abs(value - previous_value) <= CARRY_TOLERANCE:
            hits += 1
    return hits, comparisons


def carry_chains(blocks) -> list[list[int]]:
    """Split decoded blocks into carry chains at segment breaks.

    A break is a consecutive pair whose carry fidelity is below
    :data:`CHAIN_CARRY_THRESHOLD` (new segment: the frozen channels reset to
    a state the previous segment did not end with). Returns lists of block
    indices; every decoded block belongs to exactly one chain. Requires
    ``(block, decoded)`` pairs so decoding failures stay outside chains.
    """
    chains: list[list[int]] = []
    for index, (_block, decoded) in enumerate(blocks):
        if chains:
            hits, comparisons = carry_fidelity(
                blocks[chains[-1][-1]][1], decoded)
            if comparisons and hits / comparisons >= CHAIN_CARRY_THRESHOLD:
                chains[-1].append(index)
                continue
        chains.append([index])
    return chains


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
    """True for the verified sample_count=2 constant-only curve form."""
    _descriptor_count, sample_count = curve_header_counts(block)
    return sample_count == 2 and len(block.body) == 20


def constant_curve_value(block: CurveBlock) -> float | None:
    """Decoded float of a constant-only curve, else ``None``."""
    _descriptor_count, sample_count = curve_header_counts(block)
    if not (sample_count == 2 and len(block.body) == 20):
        return None
    return struct.unpack('>f', block.body[-4:])[0]


CHANNEL_TABLE_LANDMARK = (
    "001001001001001001"            # "value 9" run under 3-bit grouping
    "000000001001"                  # zero-run marker
    "000000111"
    "001001001"
)


def _body_bits(body: bytes, count: int) -> str:
    return ''.join(format(byte, '08b') for byte in body[:count])


def has_channel_table_landmark(body: bytes) -> bool:
    """True when the body opens with the shared bit-packed prologue.

    Verified on all 8 gim103 curve blocks (b0..b7) and NOT corpus-wide:
    only ~6/4474 of the shipped blocks open with it byte 0. It is the
    prologue of the gim103 family/slot-0 stream: under 3-bit MSB grouping
    it reads ``1,1,1,1,1,1,0,0,1,1,0,0,7,1,1`` (the ``24 92 49`` bytes).
    """
    prefix = CHANNEL_TABLE_LANDMARK
    return _body_bits(body, len(prefix) // 8 + 1).startswith(prefix)
