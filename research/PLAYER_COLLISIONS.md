# Original player soft-body collision volumes

`KB/chara/chr100/chr100.resource_d778dd04` is a `\0CSR` resource, not a
render mesh or a gameplay hit mesh. Its 18 records describe 16 ordinary
capsules, one capsule with two radii and one sphere. No plane occurs in it.

## Verified binary layout

The resource has version 2 at `0x04`, a big-endian record count at `0x80`,
records beginning at `0x90` with stride `0x70`, and total size
`0xa0 + count * 0x70`. Fields relative to each record are:

| Offset | Field |
| --- | --- |
| `+0x08` | shape: 1 sphere, 2 capsule, 3 capsule with two radii |
| `+0x0c` | four original enable/configuration bytes (preserved as u32) |
| `+0x10`, `+0x14` | first and second global bone IDs |
| `+0x20`, `+0x30` | first and second bone-local XYZ points (padded vec4s) |
| `+0x40` | radius (source model units) |
| `+0x44` | second radius, used only by shape 3 |
| `+0x64` | `_checkFlag`: collision domain mask |

Other fields remain uninterpreted. The sphere factory reads only the first
bone, first point and first radius: its second point/radius are not dimensions.

The examined local PS3 ELF contains the RTTI names
`ResGxSoftBodyCollision`, `TaskGxSoftBodyCollision::HitSphere`,
`HitCapsule` and `HitCapsule2` at `0xdbcd50`, `0xdbd198`, `0xdbd1c0`
and `0xdbd1e8`. The runtime factory at `0xb7ca30` dispatches the record's
`+8` shape selector at `0xb7cadc`..`0xb7cafc`. Its sphere branch reads
`+0x10`, `+0x20`, `+0x40` at `0xb7cbb8`..`0xb7cbec`. The capsule branch
reads both IDs and points and the first radius at `0xb7cca4`..`0xb7cd04`.
The shape-3 branch additionally reads `+0x44` at `0xb7cdd0`.
`HitCapsule`'s update at `0xb7cddc` transforms each endpoint through its
own live bone matrix; `HitSphere` at `0xb7bbdc` transforms only point A.
These are bounded observations of this build, not inferred from field values.

## Godot conversion

These are collision volumes with several domains, not 18 universally active
body obstacles. The adjacent `\0SSR` resource provides the selectors for each
simulation group. It has chain/cloth counts at `0x80`/`0x84`, headers starting
at `0x90`, chain header stride `0x60` and observed cloth header stride `0x190`.
A chain's tail bone is at `+0x34` and `_checkFlag` at `+0x5c`. A cloth header's
strand count is at `+8`, tail bone IDs at `+0x3c`, and `_checkFlag` at `+0x18c`.
Only these headers are decoded; the additional solver payload is untouched.

The exported query is `(CSR._checkFlag & SSR._checkFlag) != 0`:

| Simulated group | SSR tail bones | Mask | Eligible CSR records |
| --- | --- | --- | --- |
| hair | 194; 192 | `0` | none |
| short cloth chains | 72; 78 | `1` | 0, 1, 2 |
| leg ornaments | 227; 229; 231 | `2` | 2 |
| left hip cloth, three strands | 90, 95, 100 | `4` | 0, 1, 15, 16, 17 |
| cape, three strands | 225, 44, 109 | `0x108` | 0 through 14 |

CSR 9 through 14 have only bit `8`, so they affect only the cape. CSR 15 has
only bit `4`, so it affects only the left hip cloth. CSR 3 also has bit `8`:
its large volume is restricted to the cape, not classified as a universal
body collider or a proven gameplay hitbox. The cape's additional `0x100` bit
has no matching CSR volume here; other scene collision systems are outside
this decoder. Chains with no matching SSR group receive no guessed collisions.

Godot's `enable_all_child_collisions` is disabled for each chain, and its
collision paths explicitly list just the volumes selected by that group.

The exporter uses the body's CSR if present. The chr1 costume variants have
no separate CSR in this corpus, so they explicitly reuse chr100's player
collision profile. The manifest records the source path, SHA-256, shared-profile
status and every original record. Points are mapped from the source bone's
bind space into the selected body's bind space before scaling to export units.
Missing resources produce no guessed colliders; unsupported layouts/shapes or
missing referenced bones are rejected.

Spheres become bone-attached `SpringBoneCollisionSphere3D` children of
`MajinSecondaryMotion`. Capsules become `SpringBoneCollisionCapsule3D` children.
`MajinSpringCollisionUpdate`, a preceding `SkeletonModifier3D`, updates their
midpoint, orientation and full height from BOTH endpoints after animation/IK
and before the simulator. A fixed attachment to just one bone would fail at
moving elbows/hips. All generated nodes are owned by the imported scene.

Godot's capsule has a constant radius. The single two-radius source capsule
uses the smaller original radius, per the selected preview conversion. Only
CSR_01 has two radii: 10 and 15 source units (10 and 15 cm at scale 0.01),
attached to bones 204 and 207. Its Godot radius is now 10 cm. This reduces
repulsion compared with the former 15 cm conversion but does not reproduce
the wider end of the original shape-3 solver. No planes are fabricated.

Native spring `external_force` is zero. Individual joint gravity is 1 toward
`(0,-1,0)`, preserving the former global bias without adding it twice. Each
joint also stores stiffness and drag. Coupled cloth has independent metric
gravity `(0,-9.8,0)` and zero additional force. The hidden chr100 animation rig disables its spring and
collision updater; the visible body's own rig handles both. This is bone-tip
collision, not triangle-level cloth simulation, and does not guarantee that
every vertex of a wide cape panel will remain outside the body.

Validation: `python -m unittest discover -s tests -p 'test_softbody_collision.py'`.
Set `GODOT_BIN` when running `test_player_physics.py` to check engine-side
serialization, external force and the two-bone capsule update.
