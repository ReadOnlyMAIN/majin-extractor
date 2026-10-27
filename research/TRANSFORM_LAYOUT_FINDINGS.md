# Compact transform program — evidence recorded 2026-10-27

Reproduce:

```bash
python3 research/transform_layout_probe.py --json output/research/transform-layout.json
python3 research/pose_program_probe.py --json output/research/pose-program.json
```

## Structural grammar

Read the 3-bit descriptors and their payload with `decode_curve_block`, then:

```text
scalar control 0
scalar control 1
root: translation group, rotation group
bone 1..N-1 in skeleton order: translation group, rotation group, scale group

group := descriptor 1                          # implicit reference transform
       | scalar-mode other-than-1, scalar-mode other-than-1, scalar-mode other-than-1
```

All **4,436** decoded blocks across **193** packages consume the descriptor
program exactly, with zero extra descriptors, zero partial groups and zero mode-1
components inside explicit triplets. The **20** rejected blocks all have a zero
sample count and are placeholders. This explains descriptor-count changes of two:
replacing an explicit triplet by its implicit group removes two descriptors.

An entirely implicit transform program occupies `2 + 2 + 3*(N-1) = 3*N+1`
descriptors. Measured: gim533 N=11 -> 34; gim108 N=34 -> 103. The root has no scale
group. Explicit T/R/S group identity follows skeleton order; translation anchors
produce **41,015 nonzero component matches** at that exact bone/component.
These are repeated occurrences, not independent votes.

Concrete chr301 block 0 positions:

| Bone id | Translation | Rotation | Scale |
| --- | ---: | ---: | ---: |
| 0 | 2 | 5 | absent |
| 1 | 8 (implicit) | 9 | 12 (implicit) |
| 2 | 13 (implicit) | 14 | 17 (implicit) |
| 110 | 23 (implicit) | 24 | 27 (implicit) |
| 11 | 168 (implicit) | 169 | 172 (implicit) |
| 15, left-hand effector | 182 | 185 | 188 (implicit) |
| 202, left-foot effector | 266 | 269 | 272 (implicit) |

Thus neither scalar 184 nor arithmetic rotation permutations are a generic
binding rule. They resulted from interpreting group markers as scalar values.
For gim103 block 0, scalar 11 begins bone 3's rotation `(0.8415768, 0, 0)`;
scalars 21..23 translate bone id 9; 24..26 are its Euler rotation, and 27 is its
implicit scale. The old base 21 happened to align one expanded region only.

## Rotation oracle independent of the motion format

Enumerate explicit constant R groups and compare their Euler conversion to the
**local DDM quaternion**, not a quaternion derived from those same scalars.
Compare six axis orders, each in radians and degrees. Require XYZ-radians error
<1e-4 rad and **all eleven other candidates >0.01 rad**. Deduplicate repeated
angle/quaternion pairs across blocks and packages.

Result: **43 distinct noncommuting anchors** select local absolute rotations,
in radians, with column-vector matrix **Rz(z) Ry(y) Rx(x)**. No bind-delta
multiplication is applied to explicit rotations.

Examples:

- chr200 bone 10: `(π/2, -1.61950469, 0.93420506)`, angular error 2.68e-7 rad.
- chr301 bone 220: `(-1.83360291, -2.77241540, 0.09677863)`, error 3.66e-7 rad.
- chr301 bone 224: `(-1.18219888, -2.66327143, -0.72127485)`, error 4.17e-7 rad.

Scalar modes without payload are 0=0, 2=+π/2, 3=π, 4=−π/2. Mode 1 marks an
entire implicit group and is not evaluated as a scalar. Mode 5 is constant,
6 is linear, 7 is Hermite with per-second derivatives at the measured 30 Hz.
Export samples at each source frame and uses glTF LINEAR/slerp between those
samples; fractional-frame Hermite fidelity is therefore an explicit bake
approximation, not a lossless continuous-time claim.

Implicit T/R use reference transforms; implicit S is unity. Local translations
and global reference quaternions occupy the motion skeleton. Converting the
reference quaternions relative to their parents matches **3,229** DDM local
rotations, while **174** helper/constraint reference rotations differ. The DDM
local rest transforms take precedence during mesh export. Those differences
are retained as evidence, not fitted away.

## Hierarchy and IK

Skeleton header is **two bytes**, not four: N bones and declared chain count.
Then come **N-1** parent/flag byte pairs, N ids, alignment, N T/quaternion records.
The first `(0,0)` pair belongs to bone 1. The previous parser treated it as
padding and returned parent arrays shifted by one bone.

Across **193/193** skeletons, each `flag & 8` effector has a parent flagged 4
and grandparent flagged 3; their count equals the header. Names, bone ids and
anatomical offsets are unnecessary. Parent indices precede their children.

Effector T groups contain model-space targets, whereas ordinary T groups are
local translations. The current solver retains local effector reference
translations, applies the decoded local FK rotations, solves the upper/middle
rotations to the target, and preserves local effector R. It preserves the input
FK bend plane/twist; a straight chain uses a reference-frame axis. It reports
unreachable targets and refuses unsupported nonuniform ancestor scale.

First block of every package: **172 baked**, **20 placeholders**, **one package
without curves**. **13,948** IK samples, 8 unreachable targets explicitly clamped.
Maximum error to the attainable endpoint: **5.72e-13 source units**. This proves
the geometric constraints, **not** identity with HexaEngine's pole, stretch or
secondary-controller implementation. Footer controls remain raw.

## Sequence indices and names

Five u32 counts at sequence +0x80; five corresponding u32 tables at +0x94.
`0xFFFFFFFF` entries have no named record. Across **157/157 files**:

1. `0x94 + 4*sum(counts)` is exactly the first record boundary (or EOF).
2. Nonempty entries flattened across all five tables equal `0..record_count-1`.
3. For paired motion packages, table index is the package slot, **including
   skeleton slot zero**, not the zero-based index into nonempty curve blocks.

At this slot, **3,201** named-state durations equal `sample_count-1`. Five differ:
gim238 slot 2 (55 vs 35), chr321 slot 114 (58 vs 124), chr500 slots 49 (85 vs 171),
73 and 85 (69 vs 70). They remain unresolved instead of receiving a guessed clip.
Other named states point to empty slots, slots beyond this package, or composites.

Multiple tables can name one physical segment (chr100, gim108). Those aliases are
preserved. Anonymous physical helpers receive `segment_NNN`. The old claim that
all sequence records are alphabetical is false (chr370 is a counterexample).

The qstm parser formerly sliced until the following GUID, including that next
record's four-byte type word. This explains some supposed footer/footprint words;
the new slice ends at the following record's start. Qstm bytecode is still open.

## Superseded conclusions

The 2026-10-14 documents and their probes assumed flat scalar-to-bone mapping.
The following interpretations are invalidated by the compact grammar and DDM
rotation anchors: global channel base, separate bases per section, a stored
quaternion component R.w at every seventh descriptor, Euler degrees, implicit
carry through modes 0..4, fixed arm permutations, and the 184-vs-182 hand-target
rule. Legacy base/carry helpers remain only for historical probes and tests;
the maintained export path does not call them.

## Full bake and engine validation

`pose_program_probe.py --all-blocks` completed on the whole local corpus:
193 packages, **4,436 baked blocks**, **770,770 IK solves**, **7,113 clamped samples**,
20 placeholders. Maximum error to the attainable endpoint: **1.0172e-12 source
units**. No explicit scale group occurs on any ancestor of the declared IK chains
in this corpus; the solver nonetheless rejects future nonuniform/sheared cases.

Normal Godot 4.7.2 editor import plus start/middle/end pose evaluation passed:
chr100 477 animations / 1,428 poses, chr301 140 / 418, chr370 38 / 114,
chr540 83 / 249. Unclamped maximum endpoint errors are respectively 1.93, 0.99,
0.96 and 7.35 micrometers at scale 0.01. Unreachable samples retain their reported
source-solver residual rather than being presented as exact targets.

The default editor animation optimizer introduced millimeter/centimeter errors
(up to 1.545 cm on the tested chr370 pose). A companion `.glb.import` disables
`optimizer/enabled`, `compression/enabled`, and immutable-track removal, while
preserving unrelated existing import configuration. Repeating the normal editor
import with those settings produced the endpoint errors above. GLTFDocument's
per-clip immutable-track default also leaks prior poses across direct play/seek;
the preview/checker passes false, as the editor's first import stage does.

A separate interactive scene was rendered in the user's actual Godot 4.8-dev7
project with textured chr301 idle, combat and turn poses. This is visual evidence
of coherent playback, not a game-reference proof for secondary helper constraints.
