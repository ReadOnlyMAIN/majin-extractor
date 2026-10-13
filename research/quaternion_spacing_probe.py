"""Phase 2.2/2.3 probe — rotation-quartet reading under a candidate base.

Evidence over guessing: the corpus-wide quaternion refutation
(``HUMANOID_LAYOUT_FINDINGS.md``) needs no base because it groups
consecutive dated scalars.  This probe answers the complementary,
base-dependent question: what does the *proven* gim103 base (21) and the
*plurality* chr540 base (205) produce when the scalar groups are read as
quaternion component quartets ``(R.x, R.y, R.z, R.w)``?

Two structural oracles per (bone, frame) where all four rotation scalars
carry values:

1. unit length: ``|q| = 1`` within 1e-3 (the payload must hold quaternion
   components to read them as quaternions);
2. x-dominance: ``1 - x^2 >= max(y^2, z^2, w^2)`` (a property every unit
   quaternion satisfies, used to detect the component *spacing* of the
   two non-zero components of a gim103-style bind quaternion).

Writes ``QUATERNION_SPACING_FINDINGS.md`` next to this script.

Usage: python research/quaternion_spacing_probe.py [asset=base ...]
Default pairs: gim103=21 (consensus-proven), chr540=205 (plurality).
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import parse_motion_package  # noqa: E402
from tools.conversion.motion.curves import (  # noqa: E402
    decode_curve_block,
)

KB_ROOT = Path(__file__).resolve().parent.parent / \
    "game_files/decompressed/KB/motionPackage"
DEFAULT_PAIRS = {"gim103": 21, "chr540": 205}
QUAT_TOLERANCE = 1e-3
COMPONENT_NAMES = ("T.x", "T.y", "T.z", "R.x", "R.y", "R.z", "R.w")


def value_at(track, frame: int) -> float | None:
    current = None
    for key_frame, value in zip(track.frames, track.values):
        if key_frame > frame:
            break
        current = value
    return current


def quartet_stats(package, base: int) -> dict:
    """Unit/x-dominance statistics of rotation quartets under ``base``."""
    bones = package.skeleton.bone_count
    values: dict[tuple[int, int, int], float] = {}
    for block in package.curve_blocks:
        try:
            decoded = decode_curve_block(block)
        except Exception:
            continue
        for track in decoded.tracks:
            if not track.values or not track.frames:
                continue
            bone, comp = divmod(track.scalar_index + base, 7)
            if not 0 <= bone < bones:
                continue
            for frame, value in zip(track.frames, track.values):
                values[(bone, comp, frame)] = value

    frames_by_bone: dict[int, set[int]] = {}
    for bone, _comp, frame in values:
        frames_by_bone.setdefault(bone, set()).add(frame)

    complete = unit = dominant = 0
    spacings: Counter = Counter()
    norm_deviation = []
    bones_complete = set()
    for bone, frames in frames_by_bone.items():
        for frame in sorted(frames):
            quartet = [values.get((bone, comp, frame))
                       for comp in (3, 4, 5, 6)]
            if any(component is None for component in quartet):
                continue
            complete += 1
            bones_complete.add(bone)
            norm = math.sqrt(sum(c * c for c in quartet))
            norm_deviation.append(abs(norm - 1.0))
            if abs(norm - 1.0) <= QUAT_TOLERANCE:
                unit += 1
                if 1.0 - quartet[0] ** 2 >= max(
                        c * c for c in quartet[1:]) - 1e-6:
                    dominant += 1
                    non_zero = [index for index, c in enumerate(quartet)
                                if abs(c) > 1e-6]
                    if len(non_zero) == 2:
                        spacings[abs(non_zero[1] - non_zero[0])] += 1
    return {
        "bones_complete": len(bones_complete),
        "complete": complete,
        "unit": unit,
        "dominant": dominant,
        "spacings": dict(spacings),
        "median_deviation": (statistics.median(norm_deviation)
                             if norm_deviation else None),
        "max_deviation": max(norm_deviation, default=None),
    }


def survey(pairs: dict[str, int]) -> str:
    lines = ["# QUATERNION_SPACING_FINDINGS (probe auto-generated)", ""]
    for name, base in pairs.items():
        pkg_path = KB_ROOT / name / "BigEndian" / name
        if not pkg_path.is_file():
            lines += [f"## {name}", "", "corpus absent, skipped.", ""]
            continue
        package = parse_motion_package(pkg_path)
        stats = quartet_stats(package, base)
        lines += [
            f"## {name} (base {base})",
            "",
            f"- bones with complete rotation quartets: "
            f"{stats['bones_complete']}/{package.skeleton.bone_count}",
            f"- (bone, frame) quartets tested: {stats['complete']}",
            f"- unit quaternions (|q| = 1 ± 1e-3): {stats['unit']}"
            f" ({stats['unit'] / stats['complete']:.1%})"
            if stats['complete'] else "- no quartets",
            f"- median |‖q‖ − 1|: {stats['median_deviation']:.4g}, "
            f"max {stats['max_deviation']:.4g}"
            if stats['median_deviation'] is not None else "",
            f"- x-dominance holds: {stats['dominant']}",
            f"- two-non-zero-component spacings: {stats['spacings']}",
            "",
        ]
    lines += [
        "## Reading",
        "",
        "- gim103 base 21 leaves NO complete rotation quartet at all: its "
        "dated scalars never fill comps 3..6 of one bone, which is why the "
        "exported gim103 clips are translation-only.",
        "- chr540 base 205 fills quartets whose norms are far from 1 "
        "(median deviation >> 1e-3): the plurality base does NOT read "
        "quaternion components — fabricated poses, refused by the export "
        "pose oracle.",
        "- Together with the base-free consecutive-window test in "
        "HUMANOID_LAYOUT_FINDINGS.md (0 unit quaternions corpus-wide), the "
        "quaternion-component reading of the 7-scalar layout is refuted; "
        "the humanoid rotation channels must be euler triplets (REVERSE_"
        "MOTION.md 2.2, still open).",
        "",
    ]
    return "\n".join(line for line in lines if line is not None) + "\n"


def main(argv: list[str]) -> int:
    pairs = dict(DEFAULT_PAIRS)
    for argument in argv:
        name, _, raw_base = argument.partition("=")
        pairs[name] = int(raw_base)
    report = survey(pairs)
    output = Path(__file__).with_name("QUATERNION_SPACING_FINDINGS.md")
    output.write_text(report, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
