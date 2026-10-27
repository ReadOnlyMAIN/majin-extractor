#!/usr/bin/env python3
"""Reproducible decoding of every KbTarModel pass, vertex and fragment program."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
import struct
import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.research.shader_inspect import inspect_shader, _cstring
from tools.research.rsx_fp_disasm import disassemble as fragment_disassemble, format_assembly as fragment_format
from tools.research.rsx_vp_disasm import disassemble as vertex_disassemble, format_assembly as vertex_format


def decode_shader(data: bytes) -> list[dict]:
    if data[:4] != b'0bxf':
        raise ValueError('not a shader resource')
    base = data.find(b'fxbf', 0x80)
    if base < 0:
        raise ValueError('no fxbf container')
    techniques = struct.unpack_from('>H', data, base + 6)[0]
    fragment_pool = base + struct.unpack_from('>I', data, base + 12)[0]
    result = []
    for technique in range(techniques):
        entry = base + 0x18 + 20 * technique
        name_offset, descriptor_offset, passes = struct.unpack_from('>3I', data, entry)
        name = _cstring(data, base + name_offset)
        if not 1 <= passes <= 256:
            raise ValueError(f'invalid pass count {passes}')
        for variant in range(passes):
            descriptor = descriptor_offset + 96 * variant
            words = struct.unpack_from('>24I', data, base + descriptor)
            # Reuse the existing reflection parser with a view of this pass.
            view = bytearray(data)
            struct.pack_into('>H', view, base + 6, 1)
            struct.pack_into('>II', view, base + 0x18, name_offset, descriptor)
            reflection = inspect_shader(bytes(view))['programs'][0]
            vertex = data[base + words[7]:base + words[7] + words[8]]
            fragment = data[fragment_pool + words[9]:fragment_pool + words[9] + words[10]]
            if len(vertex) != words[8] or len(fragment) != words[10]:
                raise ValueError('program outside resource')
            vertex_uniforms = {int(p['metadata_word'], 16) >> 20: p['name']
                               for p in reflection['parameters']
                               if int(p['metadata_word'], 16) >> 20 >= 256}
            defaults = {}
            table = base + words[14]
            count = struct.unpack_from('>I', data, table)[0] >> 16
            for index in range(count):
                binding = struct.unpack_from('>I', data, table + 8 + index * 4)[0]
                register, value_index = binding >> 20, binding & 255
                defaults[register] = struct.unpack_from('>4f', data, base + words[13] + value_index * 16)
            constants = {}
            for parameter in reflection['parameters']:
                for offset in parameter.get('fragment_constant_offsets', []):
                    if offset in constants and constants[offset] != parameter['name']:
                        raise ValueError('overlapping runtime constant bindings')
                    constants[offset] = parameter['name']
            fp = fragment_disassemble(fragment)
            for instruction in fp:
                if 'literal_slot' in instruction:
                    instruction['constant_name'] = constants.get(instruction['literal_slot'] * 16)
            vp = vertex_disassemble(vertex)
            result.append(dict(name=f'{name}_v{variant}', technique=technique, variant=variant,
                               descriptor_relative_offset=descriptor, descriptor_words=words,
                               vertex_absolute_offset=base + words[7],
                               fragment_absolute_offset=fragment_pool + words[9],
                               vertex_uniforms=vertex_uniforms, vertex_defaults=defaults,
                               parameters=reflection['parameters'], samplers=reflection['samplers'],
                               vertex=vp, fragment=fp,
                               vertex_sha256=hashlib.sha256(vertex).hexdigest(),
                               fragment_sha256=hashlib.sha256(fragment).hexdigest()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='extracted KB/shader directory')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = []
    paths = sorted(args.source.glob('KbTarModel[1-4]'))
    if not paths:
        parser.error('no KbTarModel1..4 resources found')
    for path in paths:
        passes = decode_shader(path.read_bytes())
        for item in passes:
            prefix = args.output / item['name']
            prefix.with_suffix('.vertex.txt').write_text(vertex_format(item['vertex'], item['vertex_uniforms']) + '\n')
            fp = fragment_format(item['fragment']).splitlines()
            for index, instruction in enumerate(item['fragment']):
                if instruction.get('constant_name'):
                    fp[index] = fp[index].replace('CONST', instruction['constant_name'])
                    fp[index] = re.sub(r' ; literal=\[[^\]]*\]',
                                       ' ; runtime uniform patch', fp[index])
            prefix.with_suffix('.fragment.txt').write_text('\n'.join(fp) + '\n')
        report.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), passes=passes))
    (args.output / 'decoded.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{sum(len(item["passes"]) for item in report)} passes decoded')

if __name__ == '__main__':
    main()
