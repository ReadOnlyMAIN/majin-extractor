# Player motion selection: numeric evidence, names still unresolved

2026-10-28. No animations are renamed by this investigation.

## User observations used as search anchors

These are observations and expected strings, not names recovered from a file.

| Physical segment | User observation / expected name |
| --- | --- |
| 100, 101 | Crouch; exact names uncertain |
| 175–178, 182–185, 187–188, 227 | Ladder; exact names uncertain |
| 245, 246 | Against a wall (`hari`); exact names uncertain |
| 294, 295, 306–308 | Ordinary idle variants; exact names uncertain |
| 311 | Expected string `c100_0050_cro_turn_90_l_00` |
| 312 | Expected string `c100_0050_cro_turn_90_r_00` |
| 341, 342 | Crouch idle; exact names uncertain |

The user explicitly requested finding the two expected strings through decoding,
not assigning them as manual definitions. They are **search queries only**.

## Selection path found in the executable

The local executable contains a numeric selection path:

1. `0x2c94a0` loads a pair of u16-table pointers and stores them at character
   offsets `+0x199c` and `+0x19a0`. The first pointer pair is at `0xeb1d30`.
2. `0x2d2f50` translates a logical ID: table one uses `2*(id-1)` for IDs
   at most 98; table two uses `2*(id-100)`. Its result is passed through
   `0xbfd770` to `0x71a490`.
3. `0x719950` retains the raw offset-table base. `0x71a490` reads the word
   at `base+4*motion_number`, then adds that offset to the base.
4. Applied to the actual package, word zero is the count, word one the
   skeleton offset. Thus the checked curve entries have engine motion
   number = package slot +1 = physical segment +2.

The probe guards 20 exact instructions and checks that the resulting offsets
address the stated physical blocks. It does not follow equal boundary offsets
to invent name aliases. The active pointer pair/table still depends on runtime
state; no posture names have been assigned to those table modes.

## Measured associations

| Selected table | Logical ID | Engine number | Physical segment |
| --- | --- | --- | --- |
| First, initial pointer | 78 / 82 | 313 | 311 |
| First, initial pointer | 79 / 83 | 314 | 312 |
| First, initial pointer | 58 / 59 | 102 / 103 | 100 / 101 |
| First, pointer shifted by 196 bytes | 58 / 59 | 343 / 344 | 341 / 342 |
| Second, initial pointer | 285 / 286 / 287 / 288 / 289 | 296 / 297 / 308 / 309 / 310 | 294 / 295 / 306 / 307 / 308 |

The pointer shift is explicit at `0x2b6ce4`. It supplies a concrete link between
the two user-observed crouch-idle pairs, without asserting their original names.
The ladder and wall segment selections are also included in the JSON probe output.

For segment 311, the first scalar control changes from 0 to
`+1.5707963705062866`; for 312, from 0 to `-1.5707963705062866`.
Both have 11 frames of duration. Root Euler-Y endpoint differences are also
approximately +pi/2 and -pi/2. These measurements support opposite quarter turns;
they do not recover a text label or fully establish control semantics.

## Searches for the original strings

Neither expected string was found as ASCII, UTF-16LE or UTF-16BE in the examined
executable, chr100 sequence or chr100 curve package. The engine's multiply-then-xor
FNV-1 hash was also tried on short names and full sequence paths, in both byte
orders. The hash implementation is calibrated against `resMotionSequencePackage`
-> `0x108dc1e8`, the actual PAK resource type. No expected-name hash matched.
This negative search does not rule out other encodings or storage elsewhere.

An index scan of **891 local PAKs / 10,645 resource records** finds the chr100
motion sequence and curve package only in `static_chara.pak`. It found no second
version of either resource in those indexes. Archive payloads other than these
resources were not all re-decoded by this scan.

**Result:** numeric selection has been traced for the supplied anchors. The
exact original name strings have not been recovered. Exports retain the
`segment_NNN` names; no manual naming override has been added.

## Reproduce

```bash
python3 -m research.player_motion_selection_probe \
  --elf output/research/motion-names/EBOOT.elf \
  --pak-root game_files/package \
  --json output/research/motion-names/player-selection.json
```

The output records instruction guards, source hashes, table cell addresses,
logical IDs, motion numbers, physical offsets, measured controls and name queries.
It contains no executable or animation payload. Next evidence needed: a further
name-resolution/dictionary path, if one survives in this build, rather than
deriving strings from neighboring names or semantic descriptions.
