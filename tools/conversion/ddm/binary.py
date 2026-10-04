"""Primitive big-endian readers and packed-attribute decoders."""
from __future__ import annotations

import math
import struct


MAGIC = b"\x00ddm"
POSITION_STRIDE = 24
ATTRIBUTE_STRIDE = 16

# Version word at offset 4 -> human-readable layout family. Only version 3 is
# decoded today; the table exists so unsupported variants report their family
# instead of a bare integer.
KNOWN_DDM_VERSIONS = {
    3: "v3 (map/static + skinned character)",
}


class UnsupportedDDMVariant(RuntimeError):
    """A valid DDM whose geometry layout is not implemented yet.

    Carries structured attributes so callers can report the detected variant
    without parsing the message: ``version`` (the raw version word or ``None``),
    ``variant`` (a human-readable layout family) and ``reason`` (why it was
    rejected).
    """

    def __init__(self, reason, version=None, variant=None):
        self.version = version
        self.variant = variant or ddm_variant_name(version)
        self.reason = reason
        super().__init__(f"{self.variant}: {reason}")


def ddm_variant_name(version):
    """Return a human-readable layout family for a DDM version word."""
    if version is None:
        return "unknown DDM variant"
    return KNOWN_DDM_VERSIONS.get(version, f"unrecognized version {version}")


def be_u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def be_f32(data: bytes, off: int) -> float:
    return struct.unpack_from(">f", data, off)[0]


def be_u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def finite3(v) -> bool:
    return all(math.isfinite(x) for x in v)


def vec_sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def vec_cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def vec_len(v):
    return math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])


def vec_normalize(v):
    length = vec_len(v)
    if length <= 1e-20:
        return (0.0, 0.0, 0.0)
    return tuple(x / length for x in v)


def vec_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def decode_half2_be(data: bytes, off: int):
    return struct.unpack_from(">2e", data, off)


def decode_snorm(value: int, bits: int) -> float:
    sign = 1 << (bits - 1)
    if value & sign:
        value -= 1 << bits
    return max(-1.0, value / float(sign - 1))


def decode_packed_11_11_10(word: int):
    """Decode the observed X:11, Y:11, Z:10 signed normalized layout."""
    return (
        decode_snorm(word & 0x7FF, 11),
        decode_snorm((word >> 11) & 0x7FF, 11),
        decode_snorm((word >> 22) & 0x3FF, 10),
    )


def find_periodic_marker_run(data: bytes, marker=b"\x00\x00\x3c\x00", stride=16):
    """
    Find the longest run where marker occurs every `stride` bytes.

    In the two reference DDMs the marker is the fourth word of each 16-byte
    attribute record. The final occurrence is shared with the first word of
    the first 24-byte position record.
    """
    positions = []
    pos = 0
    while True:
        pos = data.find(marker, pos)
        if pos < 0:
            break
        positions.append(pos)
        pos += 1

    if not positions:
        return None

    posset = set(positions)
    best_start = None
    best_count = 0

    for p in positions:
        if p - stride in posset:
            continue
        count = 1
        q = p + stride
        while q in posset:
            count += 1
            q += stride
        if count > best_count:
            best_start = p
            best_count = count

    if best_start is None:
        return None

    return {
        "first_marker_offset": best_start,
        "marker_count": best_count,
        "attribute16_offset": best_start - 12,
        "position_offset": best_start + (best_count - 1) * stride,
        "vertex_count": best_count,
        "attribute16_count": best_count,
    }


def decode_vertices16(data: bytes, off: int, count: int):
    vertices = []
    for i in range(count):
        p = off + i * ATTRIBUTE_STRIDE
        normal_word, tangent_word, bitangent_word = struct.unpack_from(
            ">3I", data, p
        )
        marker = be_u32(data, p + 12)
        vertices.append({
            "index": i,
            "normal_word": normal_word,
            "tangent_word": tangent_word,
            "bitangent_word": bitangent_word,
            "normal": decode_packed_11_11_10(normal_word),
            "tangent": decode_packed_11_11_10(tangent_word),
            "bitangent": decode_packed_11_11_10(bitangent_word),
            "marker": marker,
        })
    return vertices


def score_position_stream(data: bytes, off: int, count: int):
    """
    Score the proposed 24-byte stream:
      +00 uint32/half2-like unknown
      +04 float X
      +08 float Y
      +0C float Z
      +10 0xFFFFFFFF (observed color)
      +14 half U
      +16 half V
    """
    if off < 0 or off + count * POSITION_STRIDE > len(data):
        return -1e30, {}

    finite = 0
    white = 0
    uv_reasonable = 0
    points = []

    for i in range(count):
        p = off + i * POSITION_STRIDE
        xyz = struct.unpack_from(">3f", data, p + 4)
        if finite3(xyz) and max(abs(x) for x in xyz) < 1e7:
            finite += 1
            points.append(xyz)

        if be_u32(data, p + 16) == 0xFFFFFFFF:
            white += 1

        try:
            u, v = decode_half2_be(data, p + 20)
            if math.isfinite(u) and math.isfinite(v) and abs(u) < 100 and abs(v) < 100:
                uv_reasonable += 1
        except Exception:
            pass

    if not points:
        return -1e30, {}

    bbox = []
    for axis in range(3):
        vals = [p[axis] for p in points]
        bbox.append((min(vals), max(vals)))

    ranges = [b-a for a, b in bbox]
    nonflat = sum(r > 1e-5 for r in ranges)

    score = (
        finite / count * 5.0
        + white / count * 3.0
        + uv_reasonable / count * 2.0
        + nonflat
    )

    return score, {
        "finite_ratio": finite / count,
        "white_ratio": white / count,
        "uv_reasonable_ratio": uv_reasonable / count,
        "bbox": bbox,
    }


def decode_vertices24(data: bytes, off: int, count: int):
    vertices = []
    for i in range(count):
        p = off + i * POSITION_STRIDE

        raw0 = be_u32(data, p)
        xyz = struct.unpack_from(">3f", data, p + 4)
        color = be_u32(data, p + 16)
        uv = decode_half2_be(data, p + 20)

        vertices.append({
            "index": i,
            "raw0": raw0,
            "position": xyz,
            "color": color,
            "uv": uv,
        })
    return vertices


def decode_u16_buffer(data: bytes, off: int, count: int):
    if off < 0 or off + count * 2 > len(data):
        return []
    return list(struct.unpack_from(">" + "H" * count, data, off))
