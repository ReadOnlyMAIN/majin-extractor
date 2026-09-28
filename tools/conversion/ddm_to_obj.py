#!/usr/bin/env python3
"""Deprecated compatibility entry point for :mod:`ddm_to_3d`.

The converter now primarily emits GLB, so its implementation lives in the
format-neutral ``ddm_to_3d`` module. Imports are re-exported to keep existing
scripts and third-party callers working during the rename.
"""

from __future__ import annotations

import warnings

try:
    from .ddm_to_3d import *  # noqa: F401,F403
    from .ddm_to_3d import main
except ImportError:
    from ddm_to_3d import *  # type: ignore  # noqa: F401,F403
    from ddm_to_3d import main  # type: ignore


if __name__ == "__main__":
    warnings.warn(
        "ddm_to_obj.py is deprecated; use ddm_to_3d.py instead.",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
