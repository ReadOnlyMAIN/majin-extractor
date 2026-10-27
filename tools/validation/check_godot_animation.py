#!/usr/bin/env python3
"""Validate an exported animated GLB in a real Godot 4 executable.

Usage: python tools/validation/check_godot_animation.py model.glb --godot PATH
Requires the adjacent model.motion.json and its original motionPackage.
The temporary project is isolated and no editor import settings are changed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.conversion.motion.curves import decode_curve_block
from tools.conversion.motion.ik import discover_ik_chains
from tools.conversion.motion.layout import decode_transform_layout
from tools.conversion.motion.package import parse_motion_package
from tools.conversion.motion.transforms import sample_scalar


def make_specification(report):
    package = parse_motion_package(Path(report['source_package']))
    scale = report['export_scale']
    effectors = {chain.effector for chain in discover_ik_chains(package.skeleton)}
    samples = []
    for segment in report['segments']:
        block = package.curve_blocks[segment['source_block']]
        decoded = decode_curve_block(block)
        groups = decode_transform_layout(decoded, package.skeleton.bone_count).groups
        ik_enabled = segment['ik']['policy'] != 'off'
        for frame in sorted({0, decoded.sample_count//2, decoded.sample_count-1}):
            targets = []
            if ik_enabled:
                for group in groups:
                    if group.path != 'translation' or group.implicit or group.joint not in effectors:
                        continue
                    targets.append(dict(bone=f'bone_{package.skeleton.bone_ids[group.joint]:03d}',
                                        position=[sample_scalar(t, frame, segment['frame_rate'])*scale
                                                  for t in group.tracks]))
            # Unreachable targets are already explicitly clamped and reported
            # by the source solve. Allow that measured residual, plus 0.1 mm
            # for binary32 import and Godot's animation-track optimisation.
            samples.append(dict(animation=segment['name'], time=frame/segment['frame_rate'],
                                targets=targets,
                                source_clamped_samples=segment['ik']['clamped_samples'],
                                tolerance=segment['ik']['max_target_error']*scale + 1e-4))
    return dict(samples=samples, frame_rate=report['segments'][0]['frame_rate']
                if report['segments'] else 30.0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('glb', type=Path)
    parser.add_argument('--godot', default=os.environ.get('GODOT_BIN') or shutil.which('godot')
                        or shutil.which('godot4'), help='Godot 4 executable, or set GODOT_BIN')
    parser.add_argument('--output', type=Path, help='Save engine validation JSON')
    parser.add_argument('--direct', action='store_true',
                        help='Use GLTFDocument directly instead of the normal editor import pipeline')
    args = parser.parse_args()
    if not args.godot:
        parser.error('supply --godot PATH or set GODOT_BIN')
    glb = args.glb.resolve()
    report = json.loads(glb.with_suffix('.motion.json').read_text())
    specification = make_specification(report)
    if not specification['samples']:
        parser.error('motion report contains no animation samples to validate')
    with tempfile.TemporaryDirectory(prefix='majin-godot-check-') as directory:
        project = Path(directory)
        (project / 'project.godot').write_text('config_version=5\n[application]\nconfig/name="Majin validation"\n')
        (project / 'specification.json').write_text(json.dumps(specification))
        shutil.copyfile(Path(__file__).with_name('godot_animation_check.gd'), project / 'check.gd')
        environment = dict(os.environ, XDG_DATA_HOME=str(project / 'userdata'),
                           XDG_CONFIG_HOME=str(project / 'config'),
                           XDG_CACHE_HOME=str(project / 'cache'))
        if not args.direct:
            shutil.copyfile(glb, project / 'model.glb')
            import_settings = Path(str(glb) + '.import')
            if import_settings.is_file():
                shutil.copyfile(import_settings, project / 'model.glb.import')
            print('Importing GLB through Godot editor...', flush=True)
            imported = subprocess.run([args.godot, '--headless', '--path', str(project),
                                       '--editor', '--import'], env=environment, timeout=180,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if imported.returncode or 'ERROR:' in imported.stdout:
                print(imported.stdout[-32000:])
                raise RuntimeError('Godot editor import failed')
        subprocess.run([args.godot, '--headless', '--path', str(project), '--script', 'check.gd', '--',
                        str(glb), str(project / 'specification.json'), str(project / 'result.json'),
                        'direct' if args.direct else 'editor'],
                       check=True, env=environment, timeout=180)
        result = json.loads((project / 'result.json').read_text())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
