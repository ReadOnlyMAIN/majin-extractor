#!/usr/bin/env python3
"""Research API for calibrated matcap/mask to PBR material estimation.

The converter has a lightweight ``matcap_highlight_perceptual_v2`` heuristic for immediate
GLB export. This module reserves the richer evidence-driven API for a future
calibrated implementation without coupling it to the DDM parser or GLB writer.

A red ``*_u*`` texture is evidence of a scalar/specular-style mask, but is not
proven to be roughness or metallic. A ``*_f*`` texture may be a view-dependent
matcap/reflection lookup. Core glTF 2.0 has no matcap material model, so such a
texture must not silently become base color.

See ``MATERIAL_PBR.md`` at the repository root for the research plan.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


TextureRole = Literal[
    "matcap_candidate",
    "scalar_mask_candidate",
    "normal",
    "base_color",
    "unknown",
]


@dataclass(frozen=True)
class TextureEvidence:
    """One decoded texture considered by a future material estimator."""

    path: Path
    role: TextureRole
    material_name: str | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class PBRMaterialEstimate:
    """Proposed output contract; values must include confidence/provenance."""

    roughness_factor: float | None
    metallic_factor: float | None
    confidence: float
    method: str
    evidence: tuple[TextureEvidence, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        result = asdict(self)
        for item in result["evidence"]:
            item["path"] = str(item["path"])
        return result


def estimate_material_pbr(
    evidence: list[TextureEvidence],
) -> PBRMaterialEstimate:
    """Estimate roughness/metallic values once a validated method exists."""

    raise NotImplementedError(
        "Matcap/mask to PBR estimation is a research placeholder; "
        "see MATERIAL_PBR.md."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reserved CLI for future matcap/mask to PBR estimation."
    )
    parser.add_argument(
        "analysis",
        type=Path,
        nargs="?",
        help="DDM analysis.json containing material/texture associations.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Future JSON destination for estimates and their provenance.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    parser.parse_args()
    parser.error(
        "PBR estimation is not implemented yet; see MATERIAL_PBR.md. "
        "No material values were generated."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
