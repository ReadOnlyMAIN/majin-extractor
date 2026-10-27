#!/usr/bin/env python3
"""Export one extracted KB asset folder for use in Godot."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
import subprocess
import sys

try:
    from .motion.godot import apply_scene_import_preset
except ImportError:
    from motion.godot import apply_scene_import_preset


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
        "--output-root", type=Path,
        help="Root mirroring KB (default: PROJECT/original_files/decoded, or output/decoded).",
    )
    parser.add_argument("--godot-project", type=Path,
                        help="Godot project receiving the export under original_files/decoded")
    parser.add_argument("--dry-run", action="store_true", help="Print commands only.")
    parser.add_argument("--animations", default="all",
                        help="all, none, or comma-separated animation names/globs/segment indices")
    parser.add_argument("--ik", choices=("bake", "off"), default="bake")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    if args.godot_project and not (args.godot_project / 'project.godot').is_file():
        parser.error(f"no project.godot found in {args.godot_project}")
    output_root = (args.output_root or (
        args.godot_project / 'original_files/decoded'
        if args.godot_project else Path('output/decoded'))).resolve()
    if not source.is_dir():
        parser.error(f"source folder does not exist: {args.source}")
    try:
        kb_root = kb_root_for(source)
        output_folder = output_folder_for(source, output_root)
        foliage_source = foliage_source_for(source)
    except ValueError as exc:
        parser.error(str(exc))

    # Equipment packages share the chr100 animation rig. Export the family
    # together even when one chr1xx folder was selected.
    player_family = re.fullmatch(r"chr1\d{2}", source.name) is not None and source.parent.name == "chara"
    sources = sorted(folder for folder in source.parent.iterdir()
                     if folder.is_dir() and re.fullmatch(r"chr(?:1\d{2}|7[24]\d)", folder.name)) if player_family else [source]
    if not args.dry_run:
        output_folder.mkdir(parents=True, exist_ok=True)
    for folder in sources:
        run([
            sys.executable, str(DDM_CONVERTER), str(folder), str(output_folder.parent),
            "--recursive", "--final", "--material-mode", "godot",
            "--object-mode", "auto", "--scale", "0.01",
            "--texture-root", str(kb_root),
            "--animations", args.animations if folder.name == "chr100" or not player_family else "none",
            "--ik", args.ik,
        ], dry_run=args.dry_run)
        if args.godot_project and not args.dry_run:
            for glb in sorted((output_folder.parent / folder.name).rglob('*.glb')):
                apply_scene_import_preset(glb, args.godot_project)

    if player_family:
        scene = output_folder.parent / "chr1.tscn"
        print(f"Player armor scene: {scene}")
        if not args.dry_run:
            try:
                from tools.conversion.tar_assets import write_tar_globals
            except ImportError:
                from tar_assets import write_tar_globals
            static_pak = (PROJECT_ROOT / "game_files/package/PS3_GAME/USRDIR/finalizedPS3/KB/package/static.pak"
                          if kb_root == PROJECT_ROOT / "game_files/decompressed/KB" else None)
            try:
                write_tar_globals(kb_root, output_root, static_pak)
            except FileNotFoundError as exc:
                print(f"Tar globals not installed: {exc}")
            script = PROJECT_ROOT / "godot/utility/player_armor.gd"
            shutil.copyfile(script, scene.with_name("player_armor.gd"))
            # Relative paths work when output/decoded is later copied into Godot.
            scene.write_text(
                '[gd_scene load_steps=2 format=3]\n\n'
                '[ext_resource type="Script" path="player_armor.gd" id="1"]\n\n'
                '[node name="chr1" type="Node3D"]\nscript = ExtResource("1")\n'
                'characters_directory = "res://original_files/decoded/chara"\n',
                encoding="utf-8")
            if args.godot_project:
                relative = scene.parent.relative_to(args.godot_project.resolve())
                scene.write_text(scene.read_text().replace(
                    'res://original_files/decoded/chara', 'res://' + relative.as_posix()),
                    encoding="utf-8")

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
