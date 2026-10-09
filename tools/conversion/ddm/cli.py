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


def parse_joint_indices(value: str):
    if value.strip().casefold() == "all":
        return "all"
    return [int(index.strip()) for index in value.split(",") if index.strip()]


def parse_bool(value: str):
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Expected a boolean value, got {value!r}."
    )


def ik_output_flags(mode: str):
    """Return independent offline-bake and Godot-target output switches."""
    if mode not in ("none", "bake", "godot"):
        raise ValueError(f"Unknown humanoid IK mode: {mode}")
    return mode == "bake", mode == "godot"


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
        type=parse_joint_indices,
        help=(
            "Export structurally bound humanoid joint triplets for the listed "
            "joint indices, or 'all'. Auxiliary controls outside the skeleton "
            "are omitted; transform semantics remain experimental."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-units",
        choices=("degrees", "radians", "auto", "adaptive"),
        default="radians",
        help="Angle units for the experimental XYZ rotation interpretation.",
    )
    ap.add_argument(
        "--experimental-rotation-axes",
        choices=("xyz", "xzy", "yxz", "yzx", "zxy", "zyx"),
        default="xyz",
        help=(
            "Map the three stored rotation components onto target axes. "
            "The default 'xyz' preserves the global-delta baseline."
        ),
    )
    ap.add_argument(
        "--experimental-root-rotation-source",
        choices=("local", "heading", "combined"), default="local",
        help=(
            "Choose the root rotation channel. 'local' exports bone_000's "
            "complete XYZ Euler rotation from scalars 5..7. 'heading' keeps "
            "only scalar 0's Y heading for diagnostics. 'combined' is the "
            "rejected double-turn comparison probe."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-signs",
        choices=("+++", "++-", "+-+", "+--", "-++", "-+-", "--+", "---"),
        default="+++",
        help=(
            "Signs applied to the three stored rotation components before "
            "row-vector conversion. The default preserves the baseline."
        ),
    )
    ap.add_argument(
        "--experimental-controller-bake", action="store_true",
        help=(
            "Bake matching unskinned duplicate-controller curves onto deforming "
            "joints. Disabled by default to preserve the global-delta baseline."
        ),
    )
    ap.add_argument(
        "--experimental-deforming-rotations-only", action="store_true",
        help=(
            "Animate only joints referenced by skin palettes; keep IK/control "
            "helpers at their bind transforms."
        ),
    )
    ap.add_argument(
        "--experimental-humanoid-ik", action="store_true",
        help=(
            "Deprecated alias for --experimental-humanoid-ik-mode bake."
        ),
    )
    ap.add_argument(
        "--experimental-export-ik-targets", action="store_true",
        help=(
            "Deprecated alias for --experimental-humanoid-ik-mode godot."
        ),
    )
    ap.add_argument(
        "--experimental-humanoid-ik-mode",
        choices=("none", "bake", "godot"), default="none",
        help=(
            "Humanoid IK output: 'bake' writes portable solved limb rotations; "
            "'godot' writes the same bind-pose-constrained solution and also "
            "embeds wrist/ankle targets for Godot TwoBoneIK3D; 'none' disables "
            "IK processing."
        ),
    )
    ap.add_argument(
        "--experimental-ik-target-orientation",
        choices=("source-row", "none"), default="source-row",
        help=(
            "Interpret the triplet following each IK position as an absolute "
            "source-engine row-vector Euler orientation, or ignore it. "
            "'source-row' transposes/inverts it into Godot/glTF space and "
            "cancels the selected reference pose."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-model",
        choices=(
            "local_delta_post", "local_delta_pre", "local_absolute", "global_delta",
            "global_delta_active", "global_delta_row", "global_delta_row_inverse",
            "global_reference_active", "global_reference_active_inverse",
            "global_reference_row", "global_reference_row_inverse",
        ),
        default="local_delta_post",
        help=(
            "Compose bound Euler curves with the bind pose. The default applies "
            "the delta in each joint's local bind axes."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-reference-clip", type=int,
        help=(
            "Cancel each joint's stored orientation at one reference clip "
            "endpoint before applying it in local bind space. This tests "
            "whether curves are absolute orientations around a neutral pose."
        ),
    )
    ap.add_argument(
        "--experimental-rotation-reference-frame",
        choices=("start", "end"), default="end",
        help="Endpoint used by --experimental-rotation-reference-clip.",
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
    legacy_ik_mode = (
        "godot" if args.experimental_export_ik_targets else
        "bake" if args.experimental_humanoid_ik else "none"
    )
    if (args.experimental_humanoid_ik
            and args.experimental_export_ik_targets):
        ap.error(
            "Use one --experimental-humanoid-ik-mode; bake and godot are "
            "distinct output pipelines."
        )
    if (args.experimental_humanoid_ik_mode != "none"
            and legacy_ik_mode != "none"
            and args.experimental_humanoid_ik_mode != legacy_ik_mode):
        ap.error("Conflicting experimental humanoid IK modes")
    if args.experimental_humanoid_ik_mode == "none":
        args.experimental_humanoid_ik_mode = legacy_ik_mode
    (
        args.experimental_humanoid_ik,
        args.experimental_export_ik_targets,
    ) = ik_output_flags(
        args.experimental_humanoid_ik_mode,
    )
    if (args.experimental_humanoid_ik_mode != "none"
            and not args.experimental_root_motion):
        ap.error(
            "--experimental-humanoid-ik-mode requires "
            "--experimental-root-motion"
        )
    if (args.experimental_humanoid_ik_mode != "none"
            and not args.experimental_rotation_joints):
        ap.error(
            "--experimental-humanoid-ik-mode requires "
            "--experimental-rotation-joints"
        )
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
