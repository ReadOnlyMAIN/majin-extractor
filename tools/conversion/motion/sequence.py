"""motionSequence (magic ``psmr``): the named clip index of a character.

Verified structure (REVERSE_MOTION.md):

- the body starts at ``+0x80`` with a sparse u32 slot table (large runs of
  ``0xFFFFFFFF``); its semantics are index bookkeeping and are skipped here,
- the named records follow back to back and are the payload of interest:

  - ``[u32 type_id]``  — record family code (constant per character family),
  - ``[u32 class GUID]`` — always ``0x735C64B8`` for named records,
  - ``[u64 FILETIME]`` — record creation stamp,
  - ``[u32 name_len]`` — including the trailing NUL,
  - ``[name ASCII + NUL]`` — full path ``KB/motionSequence/<name>/<clip>``,
  - ``[qstm block]`` — the state machine block attached to the record.

Records are stored in alphabetical name order.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .resource import (
    HEADER_SIZE,
    ResourceHeader,
    UnsupportedMotion,
    read_header,
)

SEQUENCE_MAGIC = b"psmr"
RECORD_TYPE_UNKNOWN = 0
CLASS_GUID = 0x735C64B8
NAME_PATH_PATTERN = re.compile(r"KB/motionSequence/[^/\x00]+/")


@dataclass
class SequenceRecord:
    """One named clip record of a motionSequence resource."""

    index: int
    type_id: int
    filetime: int
    full_name: str          # "KB/motionSequence/chr100/c100_0010_hold_climb_00"
    short_name: str         # "c100_0010_hold_climb_00"
    qstm_offset: int        # offset of the ``qstm`` block within the file
    qstm: QstmBlock | None  # decoded when the layout is recognised


@dataclass
class QstmBlock:
    """Decoded header of a ``qstm`` state-machine block."""

    magic: bytes
    version: int
    field_08: int
    field_0c: int
    field_10: int
    filetime: int
    duration_frames: int | None  # u32 at +0x83, see REVERSE_MOTION.md
    raw: bytes                   # whole block for further research


_NAME_HEAD = "KB/motionSequence/"


def _scan_records(data: bytes) -> list[tuple[int, int]]:
    """Return (type_id, record_offset) for every named record.

    The record body begins one u32 before the class GUID, which itself is
    16 bytes of (guid, filetime) before the name length prefix.
    """
    failures: list[str] = []
    hits: list[tuple[int, int]] = []
    cursor = HEADER_SIZE
    guid = data.find(b"\x73\x5c\x64\xb8", cursor)
    while guid >= 0:
        name_len = struct.unpack_from(">I", data, guid + 12)[0]
        name_raw = data[guid + 16:guid + 16 + name_len]
        if name_len >= 2 and name_raw[-1:] == b"\x00" and name_raw[2:3] == b"/":
            name_ascii = name_raw[:-1].decode("ascii", "ignore")
            if not name_ascii.startswith(_NAME_HEAD):
                failures.append(name_ascii[:48])
            else:
                type_id = struct.unpack_from(">I", data, guid - 4)[0]
                hits.append((type_id, guid - 4))
        cursor = guid + 16 + max(name_len, 1)
        guid = data.find(b"\x73\x5c\x64\xb8", cursor)
    if failures:
        raise UnsupportedMotion(
            f"unexpected named record(s) {failures[:3]!r}: format drift."
        )
    return hits


def parse_qstm(data: bytes, offset: int, limit: int) -> QstmBlock:
    """Decode the fixed header of a ``qstm`` block starting at ``offset``.

    Only the magic, the four leading words and the FILETIME are stable across
    the corpus; the variable tail is kept raw for the curve research probes.
    """
    if data[offset:offset + 4] != b"qstm":
        raise UnsupportedMotion(f"qstm magic missing at 0x{offset:X}.")
    header = struct.unpack_from(">4I", data, offset + 4)
    filetime = struct.unpack_from(">Q", data, offset + 0x28)[0]
    return QstmBlock(
        magic=b"qstm",
        version=header[0],
        field_08=header[1],
        field_0c=header[2],
        field_10=header[3],
        filetime=filetime,
        duration_frames=None,  # resolved by research/; see REVERSE_MOTION.md
        raw=data[offset:limit],
    )


def parse_motion_sequence(path: Path) -> tuple[ResourceHeader, list[SequenceRecord]]:
    """Parse a ``psmr`` motionSequence resource."""
    data = Path(path).read_bytes()
    header = read_header(data, expected_magic=b"psmr", source="motionSequence")
    hits = _scan_records(data)
    records = []
    for index, (type_id, offset) in enumerate(hits):
        guid = offset + 4
        name_len = struct.unpack_from(">I", data, guid + 12)[0]
        name = data[guid + 16:guid + 16 + name_len - 1].decode("ascii")
        full = name
        qstm_offset = data.find(b"qstm", guid + 16 + name_len)
        end_of_qstm = None
        next_guid = hits[index + 1][1] + 4 if index + 1 < len(hits) else len(data)
        short = NAME_PATH_PATTERN.sub("", full)
        qstm = None
        if qstm_offset >= 0 and qstm_offset < next_guid:
            try:
                qstm = parse_qstm(data, qstm_offset, next_guid)
            except UnsupportedMotion:
                qstm = None
        records.append(SequenceRecord(
            index=index,
            type_id=type_id,
            filetime=struct.unpack_from(">Q", data, guid + 4)[0],
            full_name=full,
            short_name=short,
            qstm_offset=qstm_offset,
            qstm=qstm,
        ))
    if not records:
        # Sequences for assets without animations (re-export placeholders)
        # legitimately carry zero records: the caller exports the skin only.
        return header, []
    return header, records
