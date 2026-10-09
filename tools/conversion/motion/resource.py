"""Shared resource header of the KB binary formats (DDM, psmr, crg, CSR).

Verified layout (REVERSE_MOTION.md covers the reverse-engineering session):

- ``+0x00``: magic u32 big-endian.
- ``+0x04``: resource version u32 big-endian (always 2 in shipped data).
- ``+0x08/+0x0c``: format-specific words.
- ``+0x28``: 64-bit Windows FILETIME build stamp (100 ns since 1601).
- ``+0x80``: first byte of the format-specific body.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class UnsupportedMotion(RuntimeError):
    """Raised when a motion resource cannot be decoded without guessing."""


@dataclass
class ResourceHeader:
    """Common 0x80-byte header of KB resources."""

    magic: int
    magic_raw: bytes
    version: int
    field_08: int
    field_0c: int
    filetime: int

    @property
    def build_time_utc(self):
        """FILETIME (100 ns since 1601-01-01) as a UTC datetime or None."""
        import datetime
        if not self.filetime:
            return None
        epoch = datetime.datetime(1601, 1, 1)
        return epoch + datetime.timedelta(microseconds=self.filetime // 10)


HEADER_SIZE = 0x80


def read_header(data: bytes, *, expected_magic: bytes | None = None,
                source: str = "resource") -> ResourceHeader:
    """Parse the common 0x80-byte resource header."""
    if len(data) < HEADER_SIZE:
        raise UnsupportedMotion(f"{source}: file smaller than the 0x80 resource header.")
    magic_raw = data[0:4]
    version = struct.unpack_from(">I", data, 0x04)[0]
    if version != 2:
        raise UnsupportedMotion(f"{source}: unsupported resource version {version}.")
    if expected_magic is not None and magic_raw[1:] != expected_magic[1:]:
        raise UnsupportedMotion(
            f"{source}: unexpected magic {magic_raw!r}, expected {expected_magic!r}."
        )
    filetime = struct.unpack_from(">Q", data, 0x28)[0]
    return ResourceHeader(
        magic=struct.unpack_from(">I", data, 0)[0],
        magic_raw=magic_raw,
        version=version,
        field_08=struct.unpack_from(">I", data, 0x08)[0],
        field_0c=struct.unpack_from(">I", data, 0x0C)[0],
        filetime=filetime,
    )


def discover_motion_paths(kb_root: Path, model_name: str) -> tuple[Path, Path] | None:
    """Locate the motionSequence/motionPackage pair of a DDM by name.

    The three resources cross-reference nothing inside their binary data;
    the engine resolves them by path convention, so does this pipeline.
    ``model_name`` is the shared base name (``chr100``, ``gim103``...).
    """
    sequence_path = kb_root / "motionSequence" / model_name / model_name
    package_path = kb_root / "motionPackage" / model_name / "BigEndian" / model_name
    if not (sequence_path.is_file() and package_path.is_file()):
        return None
    return sequence_path, package_path
