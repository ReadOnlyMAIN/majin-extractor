"""Compatibility entry point for the corrected curve-header probe.

The former A/B byte analysis was invalid: the header is two big-endian u16
counts. New research lives in :mod:`research.curve_bitstream_probe`.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .curve_bitstream_probe import DEFAULT_ASSETS, survey
except ImportError:  # direct script execution
    from curve_bitstream_probe import DEFAULT_ASSETS, survey


if __name__ == "__main__":
    report = survey(DEFAULT_ASSETS)
    output = Path(__file__).with_name("CURVE_HEADER_FINDINGS.md")
    output.write_text(report, encoding="utf-8")
    print(output)
