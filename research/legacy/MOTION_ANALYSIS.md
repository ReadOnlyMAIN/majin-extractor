# Character motion decoding — verified findings

This note records what has been **verified** about the `motionSequence` /
`motionPackage` scalar stream, cross-checked against several characters. It is a
working research log, not a specification. Every claim here was reproduced with
`research/motion_probe.py` or a direct dump; nothing is inferred from clip names
alone.

Reproduce with::

    python research/motion_probe.py chr301 --clips turn
    python research/motion_probe.py chr300 chr301 chr302 chr303 --clips compare
    python research/motion_probe.py chr301 --clips identity
    python research/motion_probe.py chr300 chr301 chr302 chr303 --clips structure

## Resource layout (unchanged, re-confirmed)

- `motionPackage/<name>/BigEndian/<name>` holds the curve stream. Header at
  `0x80` is a boundary count; `boundaries[k] = 0x80 + offset[k]`.
- The stream is `[reference skeleton][150 animation clips][metadata]`. The
  metadata segment begins with a count equal to `segments - 2`, which is how
  the clip count is recovered.
- `motionSequence/<name>/<name>` (`psmr`) holds a 150-entry *logical state-slot*
  table whose values select path records.  It must not be treated as a proven
  physical curve-segment name table.  A direct chr301 locomotion check disproves
  the former assumption that logical slot *k* always names physical segment
  *k* (details below).

## Cross-character comparison

In the turn region, `chr301` physical segment *i* equals
`chr300`/`chr302` segment *i+4*. The corresponding `turn_180_*` and
`turn_90_*` scalar bytes are **identical** across chr300/301/302/303. This is a
valid local alignment observation, but the locomotion counterexample below
means it must not be generalized into a global name-to-segment rule.

Consequence: the scalar layout is a shared humanoid format. A decoding rule
must reproduce on every chr30x. Cross-character comparisons made through the
current `clip_name_slots` are useful only where the physical/logical association
has independently been checked; matching the path strings alone does not prove
that association.

## Resolving physical curve names from `qstm`

The reported chr301 directions were a naming error, not an axis error. The
150-word table at `psmr+0x84` maps *logical state slots* to named state records;
its slot index is unrelated to the physical curve index. Applying that table
directly to the curve boundaries produced the false labels.

Each named path is followed by a `qstm` record. A verified field resolves the
indirection:

```text
qstm + 0x80 : u32 big-endian inclusive last-frame index
frame_count : last_frame + 1
qstm + 0x84 : state kind (semantics still incomplete)
```

Named `qstm` records occur in physical animation order. Matching them to the
ordered scalar segments by `frame_count`, while allowing anonymous helper
segments and empty state slots, recovers every named record on chr300/301/302/
303. For chr301 this gives 120 named states and 30 anonymous physical slots.
Nine `_pose` states map to empty physical slots; their timeline duration lives
in `qstm`, but they carry no separate scalar payload.

The corrected chr301 locomotion block is:

| physical segment | qstm frame count | resolved name | delta XYZ |
| ---: | ---: | --- | ---: |
| 10 | 45 | `move_b` | `( +1.466, +0.400, -101.310 )` |
| 11 | 45 | `move_f` | `( -0.806, +0.307, +101.107 )` |
| 12 | 45 | `move_l` | `( +100.283, +0.307, -0.884 )` |
| 13 | 45 | `move_r` | `( -100.328, +0.391, +0.614 )` |

This is exactly `back=-Z`, `forward=+Z`, `left=+X`, `right=-X` in the source
coordinate system. Physical segments 14--17 are instead `quick_b/f/l/r`, and
segment 18 is `run_a_01`. Do not compensate the old names with an axis change.

The resolver is deliberately conservative: it preserves qstm order, accepts
only an exact duration match for a non-empty segment, and otherwise permits
only an empty physical slot. Unmatched physical segments remain anonymous
rather than inheriting a neighboring name.

## The eight leading "root" scalars

For a clip: `slot0 slot1 slot2 slot3 slot4 slot5 slot6 slot7`, then three-scalar
groups afterwards.

Verified facts:

- **slot0 is the root Y heading** in radians. It is 0 for every straight
  locomotion clip (`move_f/b/l/r`, `walk`, `run`) and non-zero only when the root
  actually turns. Examples (chr301):
  - `turn_180_l_a/b` slot0: `0 -> +3.1416` (**+180°**).
  - `turn_180_r_a/b` slot0: `0 -> -3.1416` (**-180°**).
  - `turn_90_l_a/b` slot0: `0 -> +1.5708` (**+90°**).
  - `turn_90_r_a/b` slot0: `0 -> -1.5708` (**-90°**).
- **slot1 is always zero** (reserved).
- **slot2..slot4 are root translation** in centimetre-like units. They are
  continuous across consecutive clips (e.g. slot4 ends at `258.167` in one clip
  and starts at `258.167` in the next), which a rotation cannot do.
- **slot5..slot7 are bone_000's complete XYZ Euler rotation.** The decisive
  counterexample is chr301/302/303 `boredom`: slot0 remains zero for all 143
  frames while slots 5/6/7 animate respectively through approximately
  `0.298/0.022/-0.142` radians, matching the missing visible root lean/twist.
  Values beyond one turn in other clips are continuous unwrapped Euler curves,
  not invalid orientations. On every chr301 `turn_90/180` clip, the signed
  endpoint delta of slot6 matches slot0 within `2e-4` rad.

## The verified double-rotation bug

The current `decode_root_rotation(source="combined")` multiplies the extracted
heading (slot0) by a local XYZ Euler built from slots 5..7. On the `turn_180_*`
clips the local Y component (slot6) moves by the **same** amount and direction
as slot0, so the product adds the turn twice.

Measured from the corrected chr301 naming:

| clip family | `combined` probe | slots 5–7 (default) |
| --- | --- | --- |
| `turn_180_*` | 360° accumulated rotation, same final heading | ±180° |
| `turn_90_*` | ±180° | ±90° |

Two defects are visible and both are reproduced in the exported GLB:

1. **Doubled angle**: 180° clips accumulate 360°, and 90° clips export 180°.
2. **Lost root pose in the old workaround**: exporting heading alone forces
   clips such as `boredom` to identity rotation even though slots 5–7 animate.

The correct rule is therefore slots 5–7 alone. Scalar 0 remains useful as an
independent heading diagnostic, but must not be multiplied into bone_000.

## Joint triplets and the "strange frame 0" symptom

After the eight root scalars the canonical stream is 110 three-scalar groups.
The body binding is verified by bone ID across chr301/302/303. The 45-triplet
character-specific block between the head and left arm is now deliberately
unbound: its width equals 39 accessory joints plus 6 controls, but that count
does not prove a sequential joint mapping.

Observed, not yet fully interpreted:

- Most bound groups are Euler rotations, but some are declared **model-space
  positions** by the motion skeleton's effector flag, and other large triplets
  remain unclassified stride/controller data. A large magnitude is now used
  only to quarantine an unknown triplet from FK export; it never assigns a
  position semantic. This distinction matters because unwrapped rotations can
  exceed one turn while leg targets can remain below it.
- **Frame 0 is clip-specific, not a neutral pose.** For example, bone 2's first
  bound group takes 57 distinct frame-0 values across chr301's clips (from
  `-1.618` to `-0.023`). Each clip simply starts wherever that animation starts,
  so exporting frame 0 as though it were the bind pose produces the reported
  "strange frame-0 positions/rotations". Any FK export must treat frame 0 as
  data, and cancel or blend endpoints rather than assuming a neutral start.
- `infer_humanoid_rotation_bases` no longer seeds bases by bone ID. It accepts
  stable cardinal controller bases only inside the structurally proven leading
  chain. The first joint's wider statistical tolerance additionally requires
  the independent DDM bind signature `(a,b,a,b)`, the `Ry(90°)·Rz(theta)`
  frame described in §28.3. On chr301/302/303 this derives `+90° Y` for joint 1
  and `+90° X` for joint 2 without a character or bone-ID branch.

### Head and chr301 hair/tentacle block

The first four post-root triplets are shared byte-for-byte by chr301/302/303
and map coherently to bones `1`, `2`, `101`, `110`. In chr301 clip 0, bone 110
contains only `(0, 0, -0.020645)` radians (about -1.18 degrees), while bone 101
contains `(-0.0217, -0.0113, 0.1721)` radians. Those small values cannot cause
the large visible head/hair reorientation by themselves, and the dominant Z
component agrees with the planar local-Z articulation of the DDM bind chain.

The following 135 scalars tell a different story. Across all 140 non-empty
chr301 clips, **none** uses animated mode 6 or 7. Non-zero values in canonical
clips are invariant rig setup/controller constants, including `-13.4732`,
`9.2097`, and `-2.7724`; applying these sequentially as local Euler deltas to
bones 113..245 produced the apparent wrong-axis head and exploded tentacles.
The decoder therefore advances over this block to keep the verified arm
offsets, but exports no hair-bone channels from it. Hair remains in its DDM bind
pose, which is the only behavior supported by the binary evidence so far.

Mode 7 was also incorrectly sampled linearly even though its second float per
key is a derivative. One-frame segments establish `value_delta = tangent/30`;
the sampler now evaluates cubic Hermite curves with per-second tangents at the
30 Hz source rate. This changes between-key motion, not the stored axes.

### Head flexion axis: local basis, not a component swizzle

`research/fk_rotation_probe.py` makes the reported head error reproducible.
For physical clip 2 (`to_battle`) on chr301/302/303, bone 110 is bound to
scalars `17..19` and the three characters carry the same curves. The DDM bind
quaternion independently maps that joint's local Z axis to model `+X`:

```text
local X -> model +Z
local Y -> model -Y
local Z -> model +X
```

The stored motion is dominated by its third component. Treating that triplet
as a **global** delta therefore retains the wrong model-Z component. Composing
the same triplet in the DDM joint's local bind frame, with the complete animated
bone_000 rotation included, reports `(0.716,-0.697,0.053)`, 46.95°: a strong
model-X flexion component with almost no lateral Z. The old global-delta model
reports `(0.147,-0.850,-0.506)`, retaining a large sideways component. No
component permutation or character-specific sign is involved.

This evidence selects local-basis reconstruction over the old global-delta
path and generalizes byte-for-byte to chr302/303. It does **not** by itself
distinguish `bind_local * delta` from `delta * bind_local`: both move the
single-axis head channel toward model X. The post-multiply variant remains the
default experimental interpretation, while the exact pre/post composition and
multi-axis Euler order stay open pending a correctly mapped FK leg chain.

### IK controller triplets and the false axis-swizzle

The four IK position/orientation pairs are contiguous triplets, but the former
decoder started most of them two scalars late. It consequently built vectors
from one component of a controller and two components of the next controller,
which looked like an axis permutation. Clip 0 proves the correct starts, and
the values are byte-identical on chr301/302/303:

| control | chr301 position start/value | orientation start/value |
| --- | --- | --- |
| left hand | 182: `(34.895, 76.186, 1.279)` | 185: `(-0.202, -0.006, -0.430)` |
| right hand | 221: `(-31.189, 74.493, -8.434)` | 224: `(-0.200, -0.039, 0.292)` |
| left foot | 266: `(24.715, 7.343, 7.450)` | 269: `(-0.102, -0.225, 1.257)` |
| right foot | 316: `(-18.323, 7.343, -11.090)` | 319: `(0.058, 0.129, 1.479)` |

The right-foot pair is contiguous but begins two scalars into the nominal
bone-217 group, confirming that semantic controller boundaries are not always
the provisional three-scalar bone labels. `apply_experimental_humanoid_ik`
now reads these complete triplets.

### Arm transform order

The shoulder error exposed a second binding issue. Within each nine-entry arm
block, scalar order is not motion-skeleton order. It is the following mirrored
functional order:

```text
left  : 13, 35, 11, 10, 36, 251, 15, 37, 34
right : 43, 65, 41, 40, 66, 250, 45, 67, 64
```

The former sequential binding assigned the first large forearm triplet to
bone 10/40 and folded both shoulder roots over the chest. On chr301 clip 0 the
correct mapping is:

| bone/control | scalar start | frame-0 value |
| --- | ---: | --- |
| left shoulder root 10 | 173 | implicit `(0, 0, 0)` |
| left upper arm 11 | 170 | `(1.0629, 0.1433, 0)` |
| left forearm 13 | 164 | `(0.5404, -1.2845, 2.6855)` |
| left hand target 15 | 182 | `(34.8954, 76.1857, 1.2793)` |
| right shoulder root 40 | 212 | implicit `(0, 0, 0)` |
| right upper arm 41 | 209 | `(-0.9413, 0.6109, 0)` |
| right forearm 43 | 203 | `(-0.4058, 1.1561, 2.6511)` |
| right hand target 45 | 221 | `(-31.1886, 74.4931, -8.4339)` |

The same permutation and curve values reproduce on chr302/303. With this
binding, bone 10/40 stays within about 4.3 degrees of its local bind at frame 0
(the small change compensates for its animated parent), rather than roughly
178 degrees away. Bone 11/41 now receives the actual upper-arm curve, while
bone 13/43 receives the large forearm curve. The IK position/orientation
triplets also fall naturally on bones 15/37 and 45/67 instead of requiring
cross-triplet `+2` offsets.

## `turn_90_*` carries its root turn

With the corrected qstm-to-curve association, every `turn_90_l_a/b` has a
`+pi/2` slot-0 change and every `turn_90_r_a/b` a `-pi/2` change. The earlier
zero-heading observation inspected wrongly named physical segments.

## Structural patterns across the chr30x humanoids

Reproduce with::

    python research/motion_probe.py chr300 chr301 chr302 chr303 --clips structure

The probe's `structure` mode decodes the reference skeleton of each character
and reports the scalar-count variants, the triplet binding and the interleaved
auxiliary controls, all computed from the binary. The observed values:

| chr | skeleton version | bones | scalar-count variants | canonical | triplets | bound | auxiliary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chr300 | 3 | 62 | 247 / 249 / 251 / 253 | 253 | *not aligned* | — | — |
| chr301 | 4 | 81 | 332 / 334 / 336 / 338 | 338 | 110 | 41 verified + 39 unbound accessory | 30 (6 accessory) |
| chr302 | 4 | 43 | 200 / 202 / 204 / 206 | 206 | 66 | 41 verified + 1 unbound accessory | 24 (0 accessory) |
| chr303 | 4 | 62 | 257 / 259 / 261 / 263 | 263 | 85 | 61 | 24 (0 accessory) |

**Four variants per character, always spaced by two scalars.** Every character
stores the same 150 clip slots, and each clip drops zero, one or two optional
two-scalar sections relative to the canonical layout: an early controller, a
pair just before the right-leg block, and an unused suffix. This matches
`remap_humanoid_scalar_starts`, which only accepts differences of 0/2/4/6.

**Triplet alignment is the first structural gate.** With the eight root scalars
removed, `(canonical - 8) % 3 == 0` for chr301/302/303, so their transform list
is `[root scalars][joint triplets + auxiliary triplets]`. **chr300 fails this
test** (`245 % 3 = 2`) and carries skeleton version 3 instead of 4; it is a
different, older layout and must not be forced through the version-4 binding.

**The auxiliary controls sit at fixed anatomical points, not at the end.**
`infer_humanoid_joint_scalar_starts` binds the joints and leaves the rest
unbound. For chr301/302/303 the leading three unbound triplets begin at scalar
starts `20, 23, 26` (the fixed "three controls after the trunk" block); the
remaining controls follow each anatomical segment. chr301's entire 45-triplet
accessory block is unbound; six of those entries are controls in addition to
the 39 accessory-joint-sized entries.

## Bound triplets are byte-identical across characters

`--clips structure` also runs a bound-triplet comparison. Clips are aligned by
**action suffix** (the clip name with its per-character `c30x_` prefix removed),
and each bone's three curves are compared after the skeleton binding, keyed by
bone ID (`bound_triplets_named`).

- On `turn_180_l_a_00`, the 41 body bones shared by chr301, chr302 and chr303
  are compared; all curves are an exact match.
- Aggregated over all 119 action names in chr301: chr301 and chr302 share 98
  clips and **51 are fully identical** on the shared bones; chr301 and chr303
  share 98 and **35 are fully identical**.

The matching curves still strongly confirm the joint binding rule identified
in `infer_humanoid_joint_scalar_starts`, because comparison is keyed by bone ID
after selecting a locally verified pair of physical segments. It does **not**
validate the global `psmr` naming assumption. Cross-character comparisons must
use a verified physical-segment correspondence, joints by bone ID, and handle
the per-character scalar-count variants. chr301's larger skeleton (81 bones)
also needs the accessory controls absent from chr302's 43-bone skeleton.

Reproduce the aggregate in Python::

    from motion_probe import load, bound_triplets_named, character_structure

    names = ["chr301", "chr302", "chr303"]
    loaded = {name: load(name) for name in names}
    structures = {name: character_structure(*loaded[name]) for name in names}

    def curves(name, action):
        return bound_triplets_named(name, action, loaded, structures)

    clip = "0010_turn_180_l_a_00"
    shared = set(curves("chr301", clip)) & set(curves("chr302", clip))
    assert all(
        curves("chr301", clip)[bone] == curves("chr302", clip)[bone]
        for bone in shared
    )

## Generic rig binding (name-free, engine-driven)

The humanoid binder (`infer_humanoid_joint_scalar_starts`) is verified for
chr301/302/303 only: it hard-codes the arm/leg bone-ID permutations and the
accessory block. Every other rig (chr200, chr500, chr560, props) previously
failed or fell back to a four-bone prefix. The decoder now derives the binding
from the **format itself**:

### Discriminant 1 — FK vs IK is declared in the motion skeleton

Each hierarchy entry of the motion reference skeleton carries a
``constraint_flags`` byte (decoded by `decode_motion_skeleton`):

```text
0x00        plain FK bone
0x01, 0x02  IK-chain continuation
0x03        IK-chain start
0x04        IK-chain middle
& 0x08      IK effector (model-space target)
high nibble (0x40 / 0x80 / ...)  effector class bits
```

`classify_reference_roles()` turns these flags into a per-joint role. Verified
`ik_chain_layout_valid` on **34/34** characters that carry a motion resource:

| Character | bones | IK chains | effector joints | effector class bits |
| --- | ---: | ---: | --- | --- |
| chr300 | 62 | 3 | 39, 48, 58 | 0x00, 0x80, 0x80 |
| chr301 | 81 | 4 | 49, 58, 67, 77 | 0x40, 0x40, 0x80, 0x80 |
| chr500 | 199 | 4 | 96, 120, 144, 168 | 0x00 |
| chr560 | 36 | 0 | — | — |
| chr200 | 193 | 4 | 69, 99, 161, 172 | 0x40, 0x40, 0x80, 0x80 |

The chr301 effectors (bone IDs 15, 45, 202, 212) are exactly the wrist/ankle
targets that the old hard-coded `required` list in
`apply_experimental_humanoid_ik` enumerated. The generic classifier recovers
them from the format, without the list.

### Discriminant 2 — rotation vs position follows the IK role

The effectors are model-space **positions** (their triplets carry large,
unbounded magnitudes, e.g. ~1000 on chr301 clips); every other bone is a
**rotation** (bounded Euler). Magnitude alone is not a reliable discriminant
(many rotations exceed 2*pi and several moderate positions stay below it), so
rotation-vs-position is decided by the *role*, not by a threshold.

### Discriminant 3 — the root/spine chain is contiguous

Every rig stores its root and spine as the leading joints whose
``parent_index == index - 1``. `root_chain_length()` measures that chain
(chr301 = 9, chr500 = 9, chr560 = 18, chr200 = 6). The first ``chain - 1``
scalar triplets bind in order to those joints (verified 0->joint1, 1->joint2,
...). This is the generic prefix that no longer depends on the
``[0, 1, 2, 101, 110]`` humanoid signature.

### Discriminant 4 — the canonical scalar count is the aligned variant

A character stores the same clips with scalar-count variants: chr300 has
``247/249/251/253``. The richest layout is **not** the largest value — 253
leaves ``(253 - 8) % 3 == 2`` and is not triplet aligned, while 251 is.
`select_canonical_scalar_count()` picks the largest triplet-aligned variant, so
chr300's older layout binds correctly (previously the ``max`` choice rejected
it). chr301/302/303 are unchanged (their maximum was already aligned).

### Result

`decode_character_animations` now tries the verified humanoid binder first and
falls back to the generic root/spine binder, then to a root-only export. Across
the 112 extracted characters, **all 34 with a motion resource export clips**
(0 errors, 0 no-animation), including chr300 (150 clips) and chr560 (57 clips).
No step keys on the character name.

## Rotation vs position — per-bone verification

To check that the scalar triplets are read the way the engine meant them, each
bound triplet on chr301 was matched to its joint and its magnitude profile was
measured across all clips (`research/motion_semantics_probe.py`):

| joint | scalar | triplet | IK role | max over clips | median |
| ---: | ---: | ---: | --- | ---: | ---: |
| 1 | 8 | 0 | FK | 7.52 | 1.57 |
| 2 | 11 | 1 | FK | 4.34 | 1.62 |
| 47 | 164 | 52 | IK middle | 6.69 | 2.25 |
| 49 | 182 | 58 | **IK effector** | **959.54** | 53.27 |
| 48 | 185 | 59 | (partner) | **980.27** | 137.64 |
| 58 | 221 | 71 | **IK effector** | **948.66** | 74.49 |
| 57 | 224 | 72 | (partner) | **1046.75** | 111.24 |
| 67 | 254 | 82 | **IK effector** | 15.19 | 15.19 |
| 77 | 305 | 99 | **IK effector** | 34.75 | 15.19 |

Findings:

- The **hand** effectors (joints 49, 58) drive model-space positions of the order
  of ~1000 (centimeter-scale), far outside any Euler range.
- Each effector is followed by an **adjacent partner triplet** (59 after 58, 72
  after 71) with the same magnitude — the effector pair likely stores a
  *position + orientation* couple, which is why
  `apply_experimental_humanoid_ik` exports a target position with an optional
  orientation.
- The **leg** effectors (joints 67, 77) show moderate magnitudes (15–35). Their
  positions are physically smaller, so an absolute magnitude threshold is *not*
  a valid position/rotation discriminant.
- FK joints stay bounded (max ≈ 7.5, i.e. slightly above a wrapped π).

**Conclusion (confirmed for chr301, consistent across chr300/500): rotation vs
position must be decided by the skeleton's declared IK role, not by magnitude.**
A magnitude-only test either misses the moderate leg targets or misfires on
unwrapped rotations. The engine's `constraint_flags` effector bit is the only
discriminant that separates the two correctly, which is what the generic binder
uses.

### Open item (revised 2026-10-09)

The generic root/spine binder stops at the trunk, so it does not yet reach the
effectors (they always sit outside the root chain on every rig: 0/3 on chr300,
0/4 on chr301, 0/4 on chr500). Extending the binding past the root chain — to
the arms and legs that carry the IK effectors — remains the end goal, but the
2026-10-09 findings below show that the **block placement of the whole scalar
stream** is the binding's real obstacle: the hand effector verified at scalar
**184** (max 959.54 of the documented table belongs to scalar 184, re-measured
with the maintained `decode_scalar_clip` over all 140 decodable clips) while
the binder's contiguous arithmetic reads 182, and two clips of the same scalar
count (chr301 clips 0/6, both 336) hold the same channel at different
positions. The per-clip layout derivation documented in
`REVERSE_DDM.md` section 28.5 must replace the canonical-count arithmetic
before the arm/leg blocks can be bound reliably.

# 2026-10-09 findings summary

All claims below were verified against the binaries with non-destructive
probes (see `REVERSE_DDM.md` section 28 for details); confidence confirmed
unless stated.

1. **Motion reference pose spaces (was unresolved, now proven).** The motion
   package's skeleton segment stores per-joint *local* translations (3 floats)
   and *accumulated global* rotations (4 floats, |dot| match 79/81 chr301,
   149/199 chr500). FK from that pair reproduces a textbook Y-up T-pose
   (spine up to ~208 cm, arms along ±X at 160, hips x=±9.98, feet y≈7.3).
2. **IK chains are declared, not inferred.** Hierarchy flag bits: `0x1` start,
   `0x2` member, `0x4` middle, `0x8` effector, plus classes `0x40` arms and
   `0x80` legs; the segment's second byte is the chain count. chr300 has only
   3 of the usual 4 chains — the missing one is plausibly the sword arm.
3. **Root channels.** `s0` heading projection (turn clips 0→±π), `s2/s3/s4`
   root position XYZ in model space (cm); `s5/s6/s7` are bone_000 XYZ Euler
   rotation and `s6` repeats the heading delta on humanoid turn clips. A 2-bone prop (chr950, 11 scalars) gives
   the 8-channel root block `[?, ?, posXYZ, rotXYZ]`.
4. **IK targets are model-space positions.** Idle-stance sanity: left hand
   `(34.9, 76.2, 1.3)` (hanging arm), right foot `(-9.98, ≈0, 0.17)` (on the
   ground); in a walk the foot targets advance by ~+101, matching the root's
   `s4` (Z) advance.
5. **Per-clip layouts vary (root cause of "close but incorrect" exports).**
   Same-channel positions shift by insertions/omissions (±2 scalars in several
   places) independently of the clip count; one canonical arithmetic binding
   applied to every clip reads wrong channels on non-canonical clips *and*
   misses the verified effector offset even on canonical ones.
6. **Rig conventions.** Y-up, bones extend along the parent's +X; repeated
   bind-local quaternion pattern `(a, b, a, b)` composes as `Ry(90°)·Rz(θ)`
   (the ~90° idle-pose channels on the same joints are consistent).
7. **Retraction.** "Motion quaternions are the DDM local rotations" is
   replaced by 1 above; the published per-bone table's "scalar at 182"
   attribution for the hand effector is replaced by "the 959.54 magnitude
   lives at scalar 184".
8. **Open (next experiments).** (a) Exact per-clip layout rule: correlate
   scalar counts with `qstm_kind`/state names and the mode-0 block markers;
   (b) the FK-geometric constraint "leg chain end = foot target, on the
   ground" must dispose of absolute-vs-delta and the Euler order; (c) mode-7
   tangent semantics (Hermite slopes — measured nonzero and *not*
   proportional to neighbouring segment slopes, so real curve data); (d) fps
   (30 assumed, unvalidated); (e) CRG rig-graph record semantics — the
   `\0crg` files (header field `9` invariant, count at `0x80`, 0x80-byte
   records with selector/bone_a/bone_b/kind 1-4/config `[1,2,1,0]`/15 floats)
   reference the spine base (101) the attach bones 250/251 and chr500's
   107/105/135/173, and look like per-character constraint/controller
   metadata; they are decoded but unused by the pipeline.



## 2026-10-09 — scalar-level leg-region map (chr301 canonical 338, walk clips)

derek/legscan detailed sampling (per scalar, mode, value@0, max-min swing)
of the region seed=bone200 (242) and seed=bone214 (290), two walk clips
(frames 90/121 cate canon=338):

- Segment gauche 242..273 (32 scalars) = shape
  `[Z,Z][rot hip(200) 244-246][Z,K(25.389),Z,K(-9.981) 247-250][rot knee 251-253][Z,K(15.185),Z 254-256][dead 257-259][K(34.75),Z,Z 260-262][dead 263-265][Z,Z 266-267][sextet 268-273: cible swing z 249cm + orientation]`.
- Rot triplets: hip small swing (0.3..0.6 rad), knee big swing (1.1..2.8 rad)
  — '''consistent''' with hip=bone200 (morph binding 242) été knee=bone 201 read at 251 (+3).
- L'os effecteur 202 (pied) n'a pas de triplet propre dans la zone: son slot
  est le porteur [Z,Z] qui précède le sextet, l'orientation du pied vivant
  dans le triplet d'orientation du sextet.
- Le segment droit contient trois tuples stride (25.389, 0, ±9.981) et un
  (34.75, 0, 0): le seed lié de l'os 214 retombe alors sur un contrôleur,
  d'où sextet droit à +4.
- Les signatures de séquence de modes au sein d'un même count varient par clip
  (82 clips canoniques chr301 → 12 signatures): la légitimation par count seul
  est inopérante; seule la dérivation par ancres (motion_decode per-clip) est
  correcte.
- Corrélation effectuée avec les noms d'états qstm (chr301): aucune famille de
  count ne s'explique par le nom d'état (les mêmes clips nommés couvrent
  332/334/336/338): les blocs optionnels sont décidés au niveau du clip, pas
  par l'état.

Reste à démontrer (prochaine session):
1. La règle exacte d'interléavage os↔contrôleur dans les segments de jambes:
   les largeurs des blocs contrôleurs varient (4/3/3...), et la phase de la
   grille modulo 3 glisse entre les côtés gauche/droite. Piste: les K sont
   probablement des positions (cm) de pied/paramètres de pas, pas des angles
   unwrappés; il reste à concevoir une machine à états qui avance slot par
   slot en identifiant les contrôleur s par triplets invariants valore.
2. Une fois les slots exacts: composition Euler (ordre, absolu vs delta) via
   FK jambes == cibles (l'erreur restante bornée 47..142 cm étant dominée par
   les slots faux).
