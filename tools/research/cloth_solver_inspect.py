"""Bounded PS3 cloth spring-law inspection and metric-unit conversion.

Executes only the three small scalar stiffness routines, not the full original
cloth solver. Does not claim to decode geometric weights or the active grid.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
from tools.research.tar_cpu_inspect import ElfMemory
from tools.conversion.cloth_physics import extract

TOC = 0xE98EC8
ROUTINES = {'kt': 0xD699E8, 'ks': 0xD69A1C, 'kb': 0xD69A50}
EVIDENCE = {
    0xB90438: 0xEC21D024,  # (length - rest) / rest, structural first axis
    0xB905E8: 0xEC29C824,  # structural second axis
    0xB90864: 0xEC29B824,  # shear scalar input
    0xB90D54: 0xEC253824,  # bending scalar input
    0xD699F4: 0xC08300E0, 0xD69A28: 0xC08300E4, 0xD69A5C: 0xC08300E8,
    0xB8EC48: 0xED670072,  # force / cell mass
    0xB8EC54: 0xED2B01B2,  # acceleration * dt
    0xB8EC80: 0xEC0A482A,  # velocity += acceleration * dt
    0xB8ECB8: 0xED670132,  # velocity * dt
    0xB8ECE4: 0xED82502A,  # position += velocity * dt
    0xB9F558: 0x907D00D0,  # constructor gravity magnitude from lis r3,0x4475
}


def f32(value):
    return struct.unpack('>f', struct.pack('>f', value))[0]


def execute_scalar(memory: ElfMemory, kind: str, coefficient: float, strain: float):
    """Evaluate actual bounded PPC code: lfs, fcmpu, blt, lis, stw, fmuls, blr."""
    registers = {1: 0x2000, 2: TOC, 3: 0x1000}
    floats = {1: f32(strain)}
    scratch = {}
    for offset in (0xE0, 0xE4, 0xE8):
        scratch[0x1000 + offset] = struct.pack('>f', coefficient)
    pc = ROUTINES[kind]
    less = False
    for _ in range(32):
        word = memory.word(pc)
        op = word >> 26
        rt, ra = (word >> 21) & 31, (word >> 16) & 31
        immediate = struct.unpack('>h', struct.pack('>H', word & 65535))[0]
        if word == 0x4E800020:
            return floats[1]
        if op == 48:  # lfs
            address = registers.get(ra, 0) + immediate
            value = scratch.get(address)
            floats[rt] = struct.unpack('>f', value if value is not None else memory.read(address))[0]
        elif op == 63 and (word >> 1) & 1023 == 0:  # fcmpu
            less = floats[ra] < floats[(word >> 11) & 31]
        elif op == 16 and (word >> 21) & 31 == 12 and (word >> 16) & 31 == 28:
            if less:
                displacement = struct.unpack('>h', struct.pack('>H', word & 0xFFFC))[0]
                pc += displacement
                continue
        elif op == 15 and ra == 0:  # lis
            registers[rt] = (immediate << 16) & 0xFFFFFFFF
        elif op == 36:  # stw
            scratch[registers.get(ra, 0) + immediate] = struct.pack('>I', registers[rt])
        elif op == 59 and (word >> 1) & 31 == 25:  # fmuls
            floats[rt] = f32(floats[ra] * floats[(word >> 6) & 31])
        else:
            raise ValueError(f'Unsupported scalar instruction 0x{word:08x} at 0x{pc:x}')
        pc += 4
    raise ValueError('Scalar routine did not return within its instruction bound')


def metric_stiffness(coefficient, rest_length_m, scale=0.01, compression=False):
    if rest_length_m <= 0 or scale <= 0 or coefficient <= 0:
        raise ValueError('Positive length, coefficient and scale required')
    return scale * coefficient / rest_length_m * (0.5 if compression else 1.0)


def isolated_link_calibration(stiffness, mass, damping, dt=0.005):
    """Match the discrete update of ONE linear spring, with no hard limit.

    This is not a universal fit for a coupled grid. For the original angular
    drag law, damping is the coefficient for the specified direction of motion.
    """
    q = stiffness * dt * dt / mass
    if not 0 <= q < 1:
        raise ValueError('Outside stable conversion domain')
    return dict(stiffness=stiffness / (1 - q),
                velocity_retention=(1 - damping * dt / mass) / (1 - q))


def benchmark_isolated_link(memory):
    # Compress a 15-cm spring by 0.15 mm: same scalar branch throughout the
    # first step; then exercise compression/extension during oscillation.
    mass, damping, dt, rest, scale = 0.3, 0.15, 0.005, 0.15, 0.01
    source_x, source_v = -0.015, 0.0  # source cm; 0.1% initial compression
    converted_x, converted_v = source_x * scale, 0.0
    maximum_error = 0.0
    maximum_naive_error = 0.0
    naive_x, naive_v = converted_x, 0.0
    for _ in range(1000):
        source_force = -execute_scalar(memory, 'kt', 70, source_x / (rest / scale))
        source_v += (source_force - damping * source_v) / mass * dt
        source_x += source_v * dt
        k = metric_stiffness(70, rest, scale, converted_x < 0)
        fit = isolated_link_calibration(k, mass, damping, dt)
        prediction = converted_x + fit['velocity_retention'] * converted_v * dt
        new_x = prediction / (1 + fit['stiffness'] * dt * dt / mass)
        converted_v = (new_x - converted_x) / dt
        converted_x = new_x
        # The old preview: raw 70 in XPBD and source .15 as per-frame loss.
        prediction = naive_x + naive_v * (1 - damping) ** (60 * dt) * dt
        new_x = prediction / (1 + 70 * dt * dt / mass)
        naive_v = (new_x - naive_x) / dt
        naive_x = new_x
        maximum_error = max(maximum_error, abs(converted_x - source_x * scale))
        maximum_naive_error = max(maximum_naive_error, abs(naive_x - source_x * scale))
    return dict(duration_seconds=1000 * dt, initial_displacement_m=0.00015,
        converted_max_absolute_error_m=maximum_error,
        former_preview_max_absolute_error_m=maximum_naive_error,
        scope='one linear spring, prescribed mass and normal-motion drag; no collision, no length clamp, no full cloth replay')


def inspect(elf: Path, model: Path, ssr: Path):
    data = elf.read_bytes()
    memory = ElfMemory(data)
    for pc, expected in EVIDENCE.items():
        if memory.word(pc) != expected:
            raise ValueError(f'Different ELF build at 0x{pc:x}')
    if memory.read(TOC + 0x6CF0) != bytes(4):
        raise ValueError('Expected zero compression/extension threshold')
    if memory.word(0xB9F518) != 0x3C604475:
        raise ValueError('Unexpected default gravity constructor')
    report = extract(model, ssr)
    groups = []
    for cloth in report['cloths']:
        geometry = cloth['derived_rest_geometry']
        group = {'name': cloth['group'], 'coefficients': {k: cloth[k] for k in ROUTINES}}
        for label in ('along_column', 'between_adjacent_columns'):
            lengths = [edge['distance_m'] for edge in geometry[label]]
            values = [0.01 * cloth['kt'] / length for length in lengths]
            group[label] = {'length_m_range': [min(lengths), max(lengths)],
                'kt_extension_metric_range': [min(values), max(values)],
                'kt_compression_metric_range': [min(values) * .5, max(values) * .5]}
        groups.append(group)
    samples = []
    max_error = 0.0
    for kind in ROUTINES:
        coefficient = report['cloths'][0][kind]
        for strain in (-0.1, -0.01, 0.0, 0.01, 0.1):
            actual = execute_scalar(memory, kind, coefficient, strain)
            expected = coefficient * strain * (0.5 if strain < 0 else 1.0)
            max_error = max(max_error, abs(actual - expected))
            samples.append(dict(kind=kind, strain=strain, ppc_result=actual, formula_result=expected))
    return dict(elf_sha256=hashlib.sha256(data).hexdigest(),
        evidence={hex(pc): hex(word) for pc, word in EVIDENCE.items()},
        scalar_law='response = k * strain * (0.5 if strain < 0 else 1)',
        strain='(length - rest_length) / rest_length',
        metric_equivalent='K_extension = model_scale * k / rest_length_metres; K_compression = K_extension / 2',
        xpbd_compliance='alpha_extension = rest_length_metres / (model_scale * k); alpha_compression = 2 * alpha_extension',
        integration='semi-implicit Euler: v += force / cell_mass * dt; position += v * dt',
        original_longitudinal_extension_correction={'strain_threshold': struct.unpack('>f',memory.read(TOC+0x6D04))[0],
            'target_length_ratio': struct.unpack('>f',memory.read(TOC+0x6D08))[0],
            'evidence': ['0xb8e454', '0xb8e458', '0xb8e4a4', '0xb8e4ec']},
        default_gravity={'source_magnitude': 980.0, 'metric_magnitude': 9.8,
            'scope': 'TaskGxSoftBodyBase constructor default; effective chr1 runtime overrides untraced'},
        air_drag={'formula': 'F_drag = -airDamping * abs(dot(normalized(velocity), cell_normal)) * velocity',
            'evidence': ['0xb91588', '0xb915dc', '0xb913ac', '0xb913b4', '0xb91414', '0xb91450'],
            'scope': 'decoded cell force path; original normal averaging and other damping terms not replayed'},
        scalar_samples=samples, max_scalar_absolute_error=max_error, groups=groups,
        isolated_link_benchmark=benchmark_isolated_link(memory),
        limitations=['scalar laws and the identified kt force path verified; no execution of the full original cloth solver',
            'ks/kb vector paths use extra geometry-dependent projections; scalar conversion alone does not reconstruct them',
            'Godot SpringBoneSimulator3D stiffness is a different angular/pose-restoring quantity',
            'XPBD implicit integration and the original semi-implicit update have different damping/phase',
            'cell mass distribution, other damping terms, pinned-cell mapping, task scheduling and runtime gravity overrides need verification'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, default=Path('output/research/motion-names/EBOOT.elf'))
    p.add_argument('--model', type=Path, default=Path('game_files/decompressed/KB/chara/chr100/chr100'))
    p.add_argument('--ssr', type=Path, default=Path('game_files/decompressed/KB/chara/chr100/chr100.resource_29509cc1'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = inspect(args.elf, args.model, args.ssr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('max_scalar_absolute_error','groups','default_gravity')}, indent=2))


if __name__ == '__main__':
    main()
