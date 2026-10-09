#!/usr/bin/env python3
"""Export one extracted KB asset folder for use in Godot."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DDM_CONVERTER = PROJECT_ROOT / "tools/conversion/ddm_to_3d.py"
HSC_CONVERTER = PROJECT_ROOT / "tools/conversion/hsc_to_foliage.py"


def kb_root_for(source: Path) -> Path:
    """Return the closest parent named KB, including *source* itself."""
    for candidate in (source, *source.parents):
        if candidate.name.casefold() == "kb":
            return candidate
    raise ValueError(f"source folder is not below a KB directory: {source}")


def output_folder_for(source: Path, output_root: Path) -> Path:
    kb_root = kb_root_for(source)
    relative = source.relative_to(kb_root)
    if not relative.parts:
        raise ValueError("select a folder inside KB, not the KB directory itself")
    return output_root / relative


def foliage_source_for(source: Path) -> Path | None:
    expected = source / f"{source.name}_ins"
    if expected.is_file():
        return expected
    candidates = sorted(path for path in source.glob("*_ins") if path.is_file())
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"multiple HSC instance tables found: {names}")
    return candidates[0] if candidates else None


def run(command: list[str], *, dry_run: bool) -> None:
    print("+ " + " ".join(command))
    if not dry_run:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export an extracted KB folder to GLB and Godot resources."
    )
    parser.add_argument("source", type=Path, help="Folder below game_files/.../KB")
    parser.add_argument(
        "--output-root", type=Path, default=Path("output/decoded"),
        help="Root mirroring the folder layout below KB (default: output/decoded).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands only.")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    output_root = args.output_root.resolve()
    if not source.is_dir():
        parser.error(f"source folder does not exist: {args.source}")
    try:
        kb_root = kb_root_for(source)
        output_folder = output_folder_for(source, output_root)
        foliage_source = foliage_source_for(source)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.dry_run:
        output_folder.mkdir(parents=True, exist_ok=True)
    run([
        sys.executable, str(DDM_CONVERTER), str(source), str(output_folder.parent),
        "--recursive", "--final", "--material-mode", "godot",
        "--object-mode", "auto", "--scale", "0.01",
        "--texture-root", str(kb_root),
        "--experimental-root-motion",
        # Export the complete FK pose first; Godot's runtime IK modifiers then
        # solve the four limbs on top of those animated joint rotations.
        "--experimental-rotation-joints", "all",
        # Controller Euler axes live in each joint's DDM bind frame. Compose
        # there so, for example, the chr30x head's local Z curve becomes its
        # model-space X flexion instead of the old sideways model-Z tilt.
        "--experimental-rotation-model", "local_delta_post",
        "--experimental-root-rotation-source", "local",
        "--experimental-humanoid-ik-mode", "godot",
        # Positions are established, but these orientation triplets have not
        # been proven to share the HSC instance row-vector convention. Keep
        # the terminal FK rotations instead of overriding them in Godot.
        "--experimental-ik-target-orientation", "none",
    ], dry_run=args.dry_run)

    if foliage_source is None:
        print(f"No *_ins table found in {source}; foliage export skipped.")
    else:
        foliage_output = output_folder / f"{source.name}_foliage.tres"
        run([
            sys.executable, str(HSC_CONVERTER),
            str(foliage_source), str(foliage_output),
            "--require-existing-output-parent",
        ], dry_run=args.dry_run)

    print(f"Godot export written to {output_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
