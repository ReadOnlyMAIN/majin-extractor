# chr1: stiffness conversion from the original solver

## Reproducible bounded executable inspection

```sh
python -m tools.research.cloth_solver_inspect --output output/research/chr1-cloth-solver.json
python -m unittest discover -s tests -p 'test_cloth_solver_inspect.py'
```

The tool verifies selected PPC instructions against the examined local ELF,
records its SHA-256, and executes the actual three small scalar stiffness
routines using a bounded instruction interpreter. It does **not** emulate the
complete cloth solver, scene, character animation, collisions or PS3 runtime.

## Verified scalar law

The cloth vtable at `0xe2e4e0` resolves slots `0x74`, `0x78`, `0x7c` to
`0xd699e8`, `0xd69a1c`, `0xd69a50`. Those routines read runtime coefficients
at `+0xe0`, `+0xe4`, `+0xe8`: kt, ks and kb, as independently established by
reflection registration at `0xb92888`, `0xb9290c`, `0xb92978`.

All three routines implement:

```
strain = (L - L0) / L0
response = k * strain             if strain >= 0
response = 0.5 * k * strain       otherwise
```

The caller supplies normalized extension at `0xb90438`, `0xb905e8`,
`0xb90864`, `0xb90d54`. The kt path normalizes the displacement direction at
`0xb90f04`..`0xb90f40` and accumulates equal/opposite forces at
`0xb9064c`..`0xb906e0`. The ks/kb vector paths additionally involve projections
of neighboring geometry; the scalar law alone does not reproduce those paths.

When distances and forces are scaled by `s`, keeping mass and time unchanged:

```
K_metric_extension   = s * k_source / L0_metres
K_metric_compression = K_metric_extension / 2
XPBD_compliance_extension   = 1 / K_metric_extension
XPBD_compliance_compression = 2 / K_metric_extension
```

These are distance-spring coefficients in consistent scaled simulation units,
not Godot SpringBoneSimulator3D's angular/pose-restoring `stiffness`. The source
mass's SI interpretation is not established.

With `s=0.01`, the observed DDM **bone-origin links, excluding virtual tips** give:

| Group / direction | kt extension K | kt compression K |
| --- | --- | --- |
| Cape, along columns | 3.445–4.949 | 1.723–2.474 |
| Cape, between adjacent columns | 4.754–4.898 | 2.377–2.449 |
| Hip, along columns | 4.584–16.271 | 2.292–8.135 |
| Hip, between adjacent columns | 4.154–8.006 | 2.077–4.003 |

For example a 15 cm kt link has extension K `4.6667`, compression K `2.3333`,
and extension compliance `0.214286`. Copying raw `70` into a distance-spring K
makes this example fifteen times too stiff. A 5 cm virtual end tip has K `14`.
Each costume uses its own rest geometry, so converted values can differ.

## Integration and an isolated response check

The identified update `0xb8eb64` divides cell force by cell mass and applies
`v += (F/m)*dt; p += v*dt`: semi-implicit Euler. The Godot preview uses XPBD,
which changes phase and numerical damping even with physically matched K.

For **one** free mass and one linear spring, with dt `h`, damping `c` and
`q=K*h*h/m`, XPBD's discrete update can be matched by:

```
K_xpbd = K / (1-q)
velocity_retention = (1-c*h/m) / (1-q)
```

The benchmark uses actual PPC scalar responses for a 15 cm spring, a prescribed
mass 0.3, prescribed normal-motion damping 0.15 and h=0.005, over five seconds.
From 0.15 mm initial compression, maximum position error is approximately
`1.52e-12 m` with the isolated-link matched update, versus `1.72e-4 m` for
the former raw-70/per-frame-damping preview. This validates the scalar conversion
and isolated discrete update, **not the complete cape**. The per-link discrete
matching formula is not applied independently to all links of a coupled grid:
one particle participates in several links and cannot have a different velocity
retention for each of them. The production preview uses converted physical K
and decoded drag with its existing XPBD integrator instead.

## Other verified differences

`0xb8e344` corrects longitudinal links only when relative extension exceeds
`0.001` (TOC +0x6d04), targeting `1.001*L0` (TOC +0x6d08). It also corrects
velocity for that position change. This is not a symmetric stretch/compression
clamp. The preview now uses the 0.1% longitudinal extension target, permits
compression, and retains its explicit 2% transverse extension policy. Iteration
error and competing collisions can exceed those targets.

The normal-motion drag path at `0xb91588` normalizes cell velocity; at
`0xb913ac`..`0xb91458`, it subtracts
`airDamping * abs(v_hat dot cell_normal) * velocity` from force. Thus 0.15 is
not 15% velocity loss per frame. With prescribed cell mass 0.3 and motion normal
to the surface, its linear damping rate is 0.5 per second. The former preview
rate `-60*log(0.85)` was approximately 9.75 per second. In-plane motion has zero
drag from this particular term; other game damping/contact terms still exist.
The Godot conversion uses local quad tangents to approximate per-particle normals.

The soft-body constructor `0xb9f488` initializes gravity magnitude to float
bits `0x44750000`, i.e. 980 source units, and copies a `(0,-1,0)` direction
initialized at `0xb9fc34`..`0xb9fc50`. The metric default is therefore 9.8.
Runtime scene overrides are not yet traced. The preview now uses this decoded
constructor default as a separate `gravity_acceleration = Vector3(0,-9.8,0)`.
Additional cloth `external_force` is zero by default, and acceleration is
`gravity_acceleration + external_force / particle_mass`. The measured free-fall
acceleration is therefore -9.8 regardless of particle mass.

Previously, the custom cloth solver read the native spring's Y=-1 and divided
it by mass 0.3, producing approximately -3.333. That coupling is removed.
The native Godot SpringBoneSimulator3D is different: its source applies
`external_force * delta` directly in the next-tail position update, with a
per-frame drag term and no particle mass. Its inspector unit is m/s, not m/s².
See Godot's `scene/3d/spring_bone_simulator_3d.cpp`, `_process_joints`.
Consequently neither -9.8 nor -2.94 is a universal native spring value.
Native chains now express their separate preview bias through per-joint
`gravity=1`, downward, with `external_force=0`; this does not control cloth
gravity. Godot tests measure free-particle displacement `-9.8*dt²` at
masses 0.3 and 0.9, and verify gravity serialization.

## Application and limits

`godot/utility/cloth_constraints.gd` now converts stiffness per link, halves
the compression response, permits folding and uses direction-dependent drag.
Tests verify metric conversion, the actual PPC scalar routines, longitudinal
limits, Godot link compliance, compression response, anchors, gravity and collisions.

The original ks/kb projection weights, exact graph construction, cell mass
distribution, pinning and normal averaging still limit fidelity. The result
is a better-founded conversion, not an exact reproduction of the original cape.

## Air-resistance preview adjustment

Following the observation that the cloth swings too long, the preview now
multiplies the decoded drag by 4 and blends directional incidence from a 0.15
minimum to 1.0. This approximates resistance from folds/edges absent from the
coarse bone grid; neither multiplier is claimed as an extracted source value.
At mass 0.3 and cape airDamping 0.15, normal-motion damping rate becomes 2/s,
while tangential motion has a minimum rate 0.3/s. Velocity retention is
`exp(-rate*dt)`, so damping never flips the velocity at high settings.

A runtime A/B sample on `c100_0010_walk_02_00` with pose reset and the same
warm-up/sample frame counts measured cape RMS particle speed approximately
1.39 versus 0.73 m/s; hip approximately 1.20 versus 0.14 m/s. These are one-clip
observations, not a general guarantee across animations. No stiffness or gravity
change was needed for this adjustment. Godot checks separately measure normal
and tangential free-particle drag and verify serialized tuning controls.
