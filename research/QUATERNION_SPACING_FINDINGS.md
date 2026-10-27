# QUATERNION_SPACING_FINDINGS (probe auto-generated)

> Historical interpretation (2026-10-14): flat scalar mapping and carry claims
> are superseded by [the compact TRS program](TRANSFORM_LAYOUT_FINDINGS.md),
> verified 2026-10-27. Raw measurements below remain for traceability.

## gim103 (base 21)

- bones with complete rotation quartets: 0/11
- (bone, frame) quartets tested: 0
- no quartets

- x-dominance holds: 0
- two-non-zero-component spacings: {}

## chr540 (base 205)

- bones with complete rotation quartets: 95/207
- (bone, frame) quartets tested: 5144
- unit quaternions (|q| = 1 ± 1e-3): 0 (0.0%)
- median |‖q‖ − 1|: 0.899, max 2854
- x-dominance holds: 0
- two-non-zero-component spacings: {}

## Reading

- gim103 base 21 leaves NO complete rotation quartet at all: its dated scalars never fill comps 3..6 of one bone, which is why the exported gim103 clips are translation-only.
- chr540 base 205 fills quartets whose norms are far from 1 (median deviation >> 1e-3): the plurality base does NOT read quaternion components — fabricated poses, refused by the export pose oracle.
- Together with the base-free consecutive-window test in HUMANOID_LAYOUT_FINDINGS.md (0 unit quaternions corpus-wide), the quaternion-component reading of the 7-scalar layout is refuted; the humanoid rotation channels must be euler triplets (REVERSE_MOTION.md 2.2, still open).

