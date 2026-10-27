"""Bake the structurally decoded TRS program into glTF channels."""
from __future__ import annotations

import math

from .curves import decode_curve_block
from .ik import discover_ik_chains, solve_two_bone
from .layout import decode_transform_layout
from .resource import UnsupportedMotion
from .transforms import (
    SOURCE_FPS, euler_quaternion, local_reference_rotations, sample_scalar,
)


def decode_transform_clip(package, block, *, skeleton=None,
                          frame_rate=SOURCE_FPS, ik_mode="bake"):
    """Bake one physical segment; never infer a named state's composition.

    Source curves are evaluated at every source frame, then glTF interpolates
    those samples linearly (slerp for quaternions). Implicit groups explicitly
    restore the reference pose, avoiding inter-animation state leakage.
    """
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        raise ValueError("frame rate must be finite and positive")
    if ik_mode not in ("bake", "off"):
        raise ValueError("IK mode must be bake or off")
    reference = package.skeleton
    decoded = decode_curve_block(block)
    layout = decode_transform_layout(decoded, reference.bone_count)
    chains = discover_ik_chains(reference)
    parents = reference.parent_indices
    bind_t = list(reference.translations)
    bind_r = local_reference_rotations(reference)
    if skeleton is not None:
        joints = skeleton['joints']
        if len(joints) < reference.bone_count or any(
                joint['global_id'] != bone_id or joint['parent'] != parent
                for joint, bone_id, parent in zip(joints, reference.bone_ids, parents)):
            raise UnsupportedMotion("DDM and motion skeletons do not match")
        bind_t = [tuple(j['translation']) for j in joints[:reference.bone_count]]
        bind_r = [tuple(j['rotation']) for j in joints[:reference.bone_count]]
    groups = {(group.joint, group.path): group for group in layout.groups}
    frames = list(range(decoded.sample_count))
    times = [frame/frame_rate for frame in frames]
    values = {(g.joint, g.path): [] for g in layout.groups}
    stats = dict(chains=len(chains), solved_samples=0, clamped_samples=0,
                 max_target_error=0.0, max_reachable_error=0.0,
                 policy="two_bone_fk_plane" if ik_mode == "bake" else "off")
    effectors = {chain.effector for chain in chains}
    for frame in frames:
        translations = list(bind_t)
        rotations = list(bind_r)
        scales = [(1.0, 1.0, 1.0)] * reference.bone_count
        targets = {}
        for group in layout.groups:
            if group.implicit:
                continue
            vector = tuple(sample_scalar(t, frame, frame_rate) for t in group.tracks)
            joint, path = group.joint, group.path
            if path == "translation":
                if joint in effectors:
                    targets[joint] = vector
                else:
                    translations[joint] = vector
            elif path == "rotation":
                rotations[joint] = euler_quaternion(vector)
            else:
                scales[joint] = vector
        if ik_mode == "bake":
            for chain in chains:
                if chain.effector not in targets:
                    continue
                result = solve_two_bone(chain, targets[chain.effector], parents,
                                        translations, rotations, scales)
                stats['solved_samples'] += 1
                stats['clamped_samples'] += int(result['clamped'])
                stats['max_target_error'] = max(stats['max_target_error'], result['target_error'])
                stats['max_reachable_error'] = max(stats['max_reachable_error'], result['reachable_error'])
        for key in values:
            joint, path = key
            source = translations if path == 'translation' else rotations if path == 'rotation' else scales
            values[key].append(source[joint])
    channels = []
    for (joint, path), samples in values.items():
        # Two endpoint keys are sufficient for a constant channel; they also
        # reset implicit groups when switching from another animation.
        if all(sample == samples[0] for sample in samples[1:]):
            channel_times, samples = [times[0], times[-1]], [samples[0], samples[-1]]
        else:
            channel_times = times
        channels.append(dict(joint=joint, path=path, times=channel_times,
                             values=samples, interpolation='LINEAR'))
    return dict(channels=channels, sample_count=decoded.sample_count,
                duration=times[-1], frame_rate=frame_rate, ik=stats,
                scalar_mapping='compact_trs_program',
                resampled='source_frame_bake', source_slot=block.slot_index-1,
                source_block=block.block_index,
                unresolved_rig_controls=len(package.rig_controls.records)
                if package.rig_controls else 0)
