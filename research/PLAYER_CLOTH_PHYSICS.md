# chr1 cloth physics: observed source data

Source SSR distinguishes
seven CHAIN groups from two CLOTH groups; those CLOTH groups should be preserved
as surfaces. The Godot conversion now does this through an approximate XPBD
grid modifier (`godot/utility/cloth_constraints.gd`), excluding those columns
from the independent spring simulator. See `godot/README.md` for coefficient
interpretations, strain limits, collision priority and unmapped table fields.

## Reproducible extraction

Run from the repository root:

```sh
python -m research.cloth_physics_probe --output output/research/chr1-cloth-physics.json
```

Inputs are `game_files/decompressed/KB/chara/chr100/chr100` (DDM skeleton) and
`chr100.resource_29509cc1` (SSR). The report includes both input SHA-256 hashes,
raw parameters, full additional-data tables, bone IDs and derived rest geometry.
The standalone research command does not change the exported Godot simulation.
The exporter's `tools/conversion/cloth_physics.py` uses the same extraction to
populate the manifest's cloth surfaces and preserve original parameter tables.

## Cloth headers

| Property | Left hip cloth | Cape |
| --- | --- | --- |
| SSR group | cloth_0 | cloth_1 |
| Header offset | 0x330 | 0x4c0 |
| Columns × bones per column | 3 × 5 | 3 × 8 |
| Tail bones, in source column order | 90, 95, 100 | 225, 44, 109 |
| Parent of each column's first bone | 4 | 2 |
| kt / ks / kb | 70 / 50 / 50 | 70 / 50 / 50 |
| mass | 0.3 | 0.3 |
| airDamping | 0.2 | 0.15 |
| updateDelta | 0.005 | 0.005 |
| useMotionGravity | false | false |
| moveStrength, raw integer pair | 100, 100 | 60, 40 |
| Tail offset per column | (5, 0, 0) | (5, 0, 0) |
| Additional data enabled | true | true |
| Collision query mask | 0x4 | 0x108 |

The coefficient names are established by the local PS3 ELF's reflection
registrations, not guessed from their numeric values. Subsequent executable
inspection decoded their scalar relative-strain response and a metric conversion;
see CLOTH_STIFFNESS_CONVERSION.md. The ks/kb vector projections remain partly
unresolved. Mass is not asserted to be kilograms, and updateDelta is a source
parameter rather than proof of the effective runtime update frequency.

Relative CLOTH header offsets: column count +0x08, bone count +0x0c,
kt/ks/kb/mass/airDamping/updateDelta +0x10 through +0x24, simulation enabled
+0x28, update-all-matrix +0x2c, motion gravity +0x30, moveStrength +0x34/+0x38,
tail IDs +0x3c, padded tail offset vectors +0x80, additional-data flag +0x180,
and collision mask +0x18c. Header stride is 0x190.

## Additional data

The seven chain blocks start at 0x650 with stride 0x1e0. The two cloth blocks
start at 0x1370 and 0x1f80, with aligned stride 0xc10. A cloth block contains:

| Relative offset | Reflection field | Capacity |
| --- | --- | --- |
| +0x008 | radius | 256 floats |
| +0x408 | twistLimit | 256 floats |
| +0x808 | angleLimit | 256 floats |

Registration evidence in the examined ELF: chain radius/normal/twist/angle
fields at 0xb7b0c4..0xb7b2b0; cloth radius at 0xb7b35c, twist at 0xb7b3d4,
angle at 0xb7b454. The cloth additional-data constructor at 0xd68cb0 allocates
0xc0c bytes, initializing radii to zero and limit arrays to pi/2.

Observed cape limit tables include approximately 0, 10, 30, 35, 40 and 90
degrees; hip tables include 0/90 for twist and 30/90 for angle. Cape radii
range from 0 to 5 source units, hip radii from 0 to 3. At export scale 0.01,
the maxima correspond to 5 cm and 3 cm. Zero entries and defaults include
unused capacity: **the exact active-cell mapping is not yet verified**.
The observed layout is consistent with a 16-by-16 capacity, but must not be
flattened directly onto the 15 or 24 source bones without tracing the solver.
Chain blocks additionally carry 16 normal vectors.

## Derived rest geometry

Global rest positions are calculated from DDM translations and quaternions,
using the existing exporter matrix code at scale 0.01. The report records
distances along each column and between matching bones in adjacent columns,
in source column order. These are measurements, **not a decoded spring graph**.

For the cape, the 16 measured transverse distances are approximately
14.29–14.72 cm. Its 21 longitudinal distances are approximately 14.15–20.32 cm.
For the hip cloth, the 10 transverse measurements are approximately
8.74–16.85 cm and 12 longitudinal measurements are approximately 4.30–15.27 cm.
Root parent IDs describe the skeleton hierarchy; they do not by themselves
prove which solver particles are pinned. Tail offsets can introduce virtual
particles in addition to the bones.

## Remaining solver work

To reproduce the original simulation, trace construction and update of
TaskGxClothBase: cell indexing, pinned particles, longitudinal/transverse/
diagonal/bending connectivity, coefficient formulas, angular-limit axes,
time accumulation and collision response. Runtime names include width,
height, kt, ks and kb, supporting a grid interpretation, but not establishing
its precise neighbor graph.

A Godot implementation must constrain the columns together while allowing
bending and controlled deformation. Independent SpringBoneSimulator3D settings
do not provide that cross-column constraint. An additional constraint solver
or a dedicated SkeletonModifier3D is needed; directly copying 70/50/50 into
Godot spring stiffness would not reproduce the original equations.

The existing CSR collision masks remain applicable; see PLAYER_COLLISIONS.md.
