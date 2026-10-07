"""Command-line entry point for the DDM to GLB converter."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from .binary import MAGIC, UnsupportedDDMVariant
from .materials import DEFAULT_EXPORT_SCALE
from .scene import analyze_file, output_key_for

try:
    from ..blend_mask import DEFAULT_BLEND_RADIUS
except ImportError:
    from blend_mask import DEFAULT_BLEND_RADIUS


def parse_int(value: str):
    return int(value, 0)


def parse_bool(value: str):
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Expected a boolean value, got {value!r}."
    )


def iter_input_files(path: Path, recursive: bool = False):
    if path.is_file():
        yield path
    elif path.is_dir():
        candidates = path.rglob("*") if recursive else path.iterdir()
        for p in sorted(candidates):
            if p.is_file():
                yield p
    else:
        raise FileNotFoundError(path)


def output_directory_for(path: Path, out_root: Path, relative_path=None):
    return out_root / output_key_for(path, relative_path)


def prune_empty_output_directories(path: Path, out_root: Path):
    """Remove only empty directories below the configured output root."""
    boundary = out_root.resolve(strict=False)
    current = path
    while current.resolve(strict=False) != boundary:
        try:
            current.rmdir()
        except (FileNotFoundError, OSError):
            break
        current = current.parent


def main():
    ap = argparse.ArgumentParser(
        description="Experimental PS3 DDM to GLB (glTF 2.0) converter"
    )
    ap.add_argument("input", type=Path, help="DDM file or directory")
    ap.add_argument("output", type=Path, help="Output directory")
    ap.add_argument(
        "--material-mode",
        choices=("pbr", "godot", "original-godot"),
        default="pbr",
        help=(
            "Material output: 'pbr' writes a portable glTF material only. "
            "'godot' keeps the PBR fallback and additionally writes external "
            "textures, a Godot 4 shader, and a binding manifest for faithful "
            "reconstruction (blend maps, matcap/environment lookup). "
            "'original-godot' is a deprecated alias of 'godot'."
        ),
    )
    ap.add_argument("--object-mode", choices=("auto", "connected", "submeshes", "single"),
                    default="auto", help=(
                        "GLB object separation: auto keeps KB/instance assets and "
                        "character models together, and splits map geometry into "
                        "connected components. Components are reconstructed, not "
                        "original DDM authoring instances."))
    ap.add_argument(
        "--roughness",
        type=float,
        default=None,
        help=(
            "Optional GLB PBR roughness override. By default it is estimated "
            "from a reflection matcap when present, then from DDM Phong data."
        ),
    )
    ap.add_argument(
        "--blend-radius",
        type=float,
        default=DEFAULT_BLEND_RADIUS,
        help=(
            "Radius in metres over which a neighbouring material influences "
            "submesh seams in --material-mode godot. Larger values "
            "produce wider smooth transitions; the default matches roughly "
            f"five source units ({DEFAULT_BLEND_RADIUS})."
        ),
    )
    ap.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=(
            "Scan input directories recursively and preserve their relative "
            "layout below the output directory."
        ),
    )
    ap.add_argument(
        "--final",
        nargs="?",
        const=True,
        default=False,
        type=parse_bool,
        metavar="BOOL",
        help=(
            "Generate only the self-contained GLB. "
            "Diagnostic CSV/JSON files are omitted. Accepts true/false; "
            "using --final without a value means true."
        ),
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_EXPORT_SCALE,
        help=(
            "Multiplier applied to exported positions. The default "
            f"({DEFAULT_EXPORT_SCALE}) converts the observed centimeter-like "
            "DDM coordinates to meters."
        ),
    )

    ap.add_argument("--vertex-count", type=int)
    ap.add_argument(
        "--animation-clips",
        type=lambda value: [int(index.strip()) for index in value.split(",") if index.strip()],
        help=(
            "Inspect only these zero-based motion clip indices, e.g. 71,72,73. "
            "Scalar curves are validated but rig binding remains unsupported."
        ),
    )
    ap.add_argument(
        "--experimental-root-motion", action="store_true",
        help=(
            "Export the selected chr300 clips' proven root translation and Y heading; "
            "joint rotations remain intentionally omitted."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-joints",
        type=lambda value: [int(index.strip()) for index in value.split(",") if index.strip()],
        help=(
            "Export experimental absolute XYZ Euler rotations for the "
            "listed joint indices. Requires --experimental-root-motion and "
            "a 5+4*joints descriptor layout; the fourth scalar per joint is "
            "ignored pending identification."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-units", choices=("degrees", "radians", "auto"),
        default="degrees",
        help="Angle units for the experimental XYZ rotation interpretation.",
    )
    ap.add_argument("--position-offset", type=parse_int)
    ap.add_argument("--index-offset", type=parse_int)
    ap.add_argument(
        "--index-count",
        "--index-size",
        dest="index_count",
        type=parse_int,
        help=(
            "Number of uint16 indices. --index-size is retained as a "
            "deprecated compatibility alias."
        ),
    )
    ap.add_argument(
        "--texture-root",
        type=Path,
        help=(
            "Root containing referenced texture folders (normally inferred "
            "from the input path)."
        ),
    )
    ap.add_argument(
        "--no-textures",
        action="store_true",
        help="Parse material names but do not resolve or convert XET textures.",
    )
    ap.add_argument("--debug", action="store_true")

    args = ap.parse_args()
    if args.material_mode == "original-godot":
        # Deprecated alias kept for existing launch configs and scripts.
        args.material_mode = "godot"
    if args.material_mode == "godot" and args.no_textures:
        ap.error("--material-mode godot cannot be used with --no-textures")
    if args.experimental_rotation_joints and not args.experimental_root_motion:
        ap.error("--experimental-rotation-joints requires --experimental-root-motion")
    if not math.isfinite(args.scale) or args.scale <= 0.0:
        ap.error("--scale must be a finite number greater than zero.")
    if args.roughness is not None and (
        not math.isfinite(args.roughness) or not 0.0 <= args.roughness <= 1.0
    ):
        ap.error("--roughness must be between 0.0 and 1.0.")
    args.output.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    unsupported = 0
    failed = 0
    input_is_directory = args.input.is_dir()
    input_files = list(iter_input_files(args.input, args.recursive))

    for path in input_files:
        try:
            data = path.read_bytes()
            if len(data) < 8 or data[:4] != MAGIC:
                if args.debug:
                    print(f"[SKIP] {path}: magic mismatch")
                skipped += 1
                continue

            relative_path = (
                path.relative_to(args.input)
                if input_is_directory
                else None
            )
            try:
                analyze_file(
                    path,
                    args.output,
                    args,
                    relative_path=relative_path,
                )
            except UnsupportedDDMVariant as exc:
                detail = exc.variant
                if exc.version is not None:
                    detail += f", version={exc.version}"
                print(f"[UNSUPPORTED] {path} ({detail}): {exc.reason}")
                unsupported += 1
                prune_empty_output_directories(
                    output_directory_for(path, args.output, relative_path),
                    args.output,
                )
                continue
            processed += 1

        except Exception as exc:
            print(f"[ERROR] {path}: {exc}")
            failed += 1
            relative_path = (
                path.relative_to(args.input)
                if input_is_directory
                else None
            )
            prune_empty_output_directories(
                output_directory_for(path, args.output, relative_path),
                args.output,
            )
            if args.debug:
                raise

    print(
        f"\nSummary: {processed} decoded, {unsupported} unsupported, {failed} failed, "
        f"{skipped} non-DDM files skipped."
    )
    if processed == 0:
        if unsupported:
            print("No supported geometry decoded; unsupported DDM files were skipped.")
        else:
            print("No DDM file decoded.")
