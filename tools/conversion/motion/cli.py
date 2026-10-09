"""Inspection CLI: classify the motion resources of an asset.

Usage::

    python -m tools.conversion.motion --inspect chr100
    python -m tools.conversion.motion --inspect --all game_files/decompressed/KB
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .package import parse_motion_package
from .resource import UnsupportedMotion, discover_motion_paths
from .sequence import parse_motion_sequence


def inspect(kb_root: Path, name: str, curve_stats: bool = False) -> int:
    """Print the motion taxonomy of one asset; return a process exit code."""
    paths = discover_motion_paths(kb_root, name)
    if paths is None:
        print(f"{name}: no motionSequence/motionPackage pair found.")
        return 1
    sequence_path, package_path = paths
    print(f"{name}:")
    try:
        _header, records = parse_motion_sequence(sequence_path)
        print(f"  records: {len(records)}")
        for record in records[:8]:
            print(f"    - {record.short_name}")
        if len(records) > 8:
            print(f"    ... ({len(records) - 8} more)")
    except UnsupportedMotion as exc:
        print(f"  sequence: UNSUPPORTED ({exc})")
        records = []
    try:
        package = parse_motion_package(package_path)
    except UnsupportedMotion as exc:
        print(f"  package: UNSUPPORTED ({exc})")
        return 1
    skeleton = package.skeleton
    print(
        "  skeleton: bones=%d chains=%d timeline=%s"
        % (
            skeleton.bone_count if skeleton else -1,
            skeleton.chain_count if skeleton else -1,
            package.timeline.segment_count if package.timeline else None,
        )
    )
    print(f"  curve blocks: {len(package.curve_blocks)}")
    if curve_stats:
        from collections import Counter

        types = Counter(block.header[0] for block in package.curve_blocks)
        print(f"  curve types: {dict(sorted(types.items()))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="asset name(s) such as chr100")
    parser.add_argument("--kb-root", type=Path,
                        default=Path("game_files/decompressed/KB"),
                        help="KB root containing motionSequence/ and motionPackage/")
    parser.add_argument("--all", action="store_true",
                        help="inspect every asset that has a motion pair")
    parser.add_argument("--curve-stats", action="store_true",
                        help="add curve block type statistics")
    args = parser.parse_args(argv)
    if args.all:
        names = sorted(
            path.name for path in (args.kb_root / "motionPackage").iterdir()
            if path.is_dir()
        )
    elif args.names:
        names = args.names
    else:
        parser.error("give asset names or --all")
        return 2
    fail = 0
    for name in names:
        fail |= inspect(args.kb_root, name, args.curve_stats)
    return fail


if __name__ == "__main__":
    sys.exit(main())
