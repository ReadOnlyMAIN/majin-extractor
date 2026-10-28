# chr100: name availability and rejected offset hypothesis (2026-10-28)

## Refutation and rollback

The proposed shared-offset mapping is rejected. The user observed that
`segment_294`, renamed `c100_0010_run_wall_01_00`, is a yawning idle variant.
Equal offsets and matching qstm durations do **not** establish animation identity.
The 54 duration matches across the corpus are structural observations only.

All 23 speculative names/aliases have been removed from the exporter. The
four previously anonymous segments remain `segment_027`, `segment_111`,
`segment_217`, and `segment_294`. The installed GLB, motion report, and
post-import script were restored byte-for-byte from pre-change backups.
The additional Godot name-restoration changes were also removed from source.

## Current bounded observations

- 414 named sequence records; the existing direct-slot export resolves 391.
- 367 physical curve blocks; 86 have no direct named record in the five tables.
- Export restored to 477 entries (391 current names/aliases + 86 anonymous).
- 23 named states have zero storage spans at their indexed slots. They remain
  unresolved; neither absence of motion nor forward aliases is established.
- The source sequence has no exact `c100_0010_walk_03_00` literal.
- No `c100_...` literal was found in the examined decrypted EBOOT. This does
  not exclude numeric IDs or encoded references elsewhere.
- Extracted motion resources match those in the local `static_chara.pak`.

Existing direct-slot names have not all been visually checked. Numeric pose
checks and successful Godot imports do not independently prove name semantics.
The user-reported yawning motion is an observation, not a recovered source name.

## Required evidence before renaming

Establish the actual runtime state-to-curve selection from game code/data or
an independent game reference. Slot adjacency, equal durations, and equal
boundary offsets alone are insufficient. Keep unknown names as `segment_NNN`.
DLC extraction remains deferred.

```bash
python3 -m research.motion_name_probe --asset chr100 \
  --json output/research/motion-names/chr100-names.json
python3 -m unittest discover -s tests
```

The probe describes the current export mapping; it does not certify that
mapping semantically and does not assign names to zero-span entries.

## Subsequent numeric investigation

The user supplied semantic anchors, and specifically asked that the expected
311/312 names be found in decoding rather than manually defined. The resulting
PPU numeric selection path and negative name searches are documented in
[PLAYER_MOTION_SELECTION_FINDINGS.md](PLAYER_MOTION_SELECTION_FINDINGS.md).
This investigation adds no exported names or manual overrides.
