#!/usr/bin/env python3
"""Command-line entry point for the DDM to GLB (glTF 2.0) converter.

The implementation lives in the :mod:`tools.conversion.ddm` package: primitive
reads (:mod:`~tools.conversion.ddm.binary`), material/texture handling
(:mod:`~tools.conversion.ddm.materials`), topology (:mod:`~tools.conversion.ddm.geometry`),
skinned characters (:mod:`~tools.conversion.ddm.skinned`), scene export
(:mod:`~tools.conversion.ddm.scene`) and this CLI (:mod:`~tools.conversion.ddm.cli`).

This module re-exports the package's public names for backward compatibility,
so existing imports (``from tools.conversion import ddm_to_3d as ddm``) keep
working. See ``REVERSE_DDM.md`` and ``ROADMAP.md`` for the reverse-engineering
status.

Usage:
  python tools/conversion/ddm_to_3d.py INPUT OUTPUT_DIR
  python tools/conversion/ddm_to_3d.py INPUT_DIR OUTPUT_DIR [--recursive]

Important:
This is an EXPERIMENTAL decoder. Unsupported DDM variants are reported instead
of being decoded with reference-file-specific offsets.
"""

from __future__ import annotations

try:
    from .ddm import *  # noqa: F401,F403
    from .ddm import main
    from .ddm import __all__ as _ddm_all
except ImportError:
    from ddm import *  # type: ignore  # noqa: F401,F403
    from ddm import main  # type: ignore
    from ddm import __all__ as _ddm_all  # type: ignore

__all__ = list(_ddm_all)


if __name__ == "__main__":
    main()
