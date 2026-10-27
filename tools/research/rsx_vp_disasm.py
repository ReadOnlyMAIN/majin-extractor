#!/usr/bin/env python3
"""Decode the RSX vertex ISA used by KbTarModel, including predicates/branches.

Bit layouts cross-checked with RPCS3's RSXVertexProgram.h (see research notes).
This is a decoder, not a claim of bit-exact PS3 floating-point emulation.
"""
from __future__ import annotations
import struct

CHANNELS = 'xyzw'
VEC = 'NOP MOV MUL ADD MAD DP3 DPH DP4 DST MIN MAX SLT SGE ARL FRC FLR SEQ SFL SGT SLE SNE STR SSG NULL NULL TXL'.split()
SCA = 'NOP MOV RCP RCC RSQ EXP LOG LIT BRA BRI CAL CLI RET LG2 EX2 SIN COS BRB CLB PSH POP'.split()
VECTOR_SOURCES = {'MOV': (0,), 'MUL': (0, 1), 'ADD': (0, 2), 'MAD': (0, 1, 2),
                  'DP3': (0, 1), 'DPH': (0, 1), 'DP4': (0, 1), 'MIN': (0, 1),
                  'MAX': (0, 1), 'SLT': (0, 1), 'SGE': (0, 1), 'ARL': (0,),
                  'FRC': (0,), 'FLR': (0,), 'SEQ': (0, 1), 'SGT': (0, 1),
                  'SLE': (0, 1), 'SNE': (0, 1), 'SSG': (0,)}


def disassemble(payload: bytes) -> list[dict]:
    if len(payload) % 16:
        raise ValueError('unaligned vertex program')
    result = []
    for offset in range(0, len(payload), 16):
        d0, d1, d2, d3 = struct.unpack_from('>4I', payload, offset)
        fields = [((d1 & 255) << 9) | (d2 >> 23),
                  (d2 >> 6) & 0x1FFFF, ((d2 & 63) << 11) | (d3 >> 21)]
        sources = []
        for index, word in enumerate(fields):
            kind = {1: 'temporary', 2: 'input', 3: 'constant'}.get(word & 3, 'unknown')
            register = ((word >> 2) & 63 if kind == 'temporary'
                        else (d1 >> 8) & 15 if kind == 'input' else (d1 >> 12) & 1023)
            sources.append(dict(kind=kind, register=register,
                                swizzle=''.join(CHANNELS[(word >> sh) & 3] for sh in (14, 12, 10, 8)),
                                negate=bool(word & (1 << 16)), absolute=bool(d0 & (1 << (21 + index))),
                                indexed=bool(d3 & 2) if kind == 'constant' else False))
        vector_id, scalar_id = (d1 >> 22) & 31, (d1 >> 27) & 31
        if vector_id >= len(VEC) or scalar_id >= len(SCA):
            raise ValueError(f'unknown vertex opcode at {offset:#x}')
        vector, scalar = VEC[vector_id], SCA[scalar_id]
        item = dict(slot=offset // 16, byte_offset=offset, vector=vector, scalar=scalar,
                    sources=sources, vector_mask=''.join(CHANNELS[k] for k in range(4) if d3 & (1 << (16-k))),
                    scalar_mask=''.join(CHANNELS[k] for k in range(4) if d3 & (1 << (20-k))),
                    vector_temp=(d0 >> 15) & 63, scalar_temp=(d3 >> 7) & 63,
                    output=(d3 >> 2) & 31, vector_result=bool(d0 & (1 << 30)),
                    condition_mask=(d0 >> 10) & 7, condition_test=bool(d0 & (1 << 13)),
                    condition_swizzle=''.join(CHANNELS[(d0 >> sh) & 3] for sh in (8, 6, 4, 2)),
                    condition_register=(d0 >> 25) & 1,
                    condition_update=bool(d0 & ((1 << 14) | (1 << 29))),
                    address_register=(d0 >> 24) & 1, address_component=CHANNELS[d0 & 3],
                    saturate=bool(d0 & (1 << 26)), end=bool(d3 & 1),
                    branch_target=(((d0 >> 23) & 1) << 9) | ((d2 & 63) << 3) | (d3 >> 29),
                    raw_words=[hex(d0), hex(d1), hex(d2), hex(d3)])
        result.append(item)
        if item['end']:
            break
    return result


def format_assembly(instructions: list[dict], constants: dict[int, str] | None = None) -> str:
    constants = constants or {}
    lines = []
    for item in instructions:
        sources = []
        for source in item['sources']:
            reg = source['register']
            text = ('r' + str(reg) if source['kind'] == 'temporary' else
                    'v' + str(reg) if source['kind'] == 'input' else
                    constants.get(reg, 'c' + str(reg)))
            if source['indexed']:
                text += '[a]'
            text += '.' + source['swizzle']
            if source['absolute']:
                text = 'abs(' + text + ')'
            if source['negate']:
                text = '-' + text
            sources.append(text)
        ops = []
        for key in ('vector', 'scalar'):
            op = item[key]
            if op == 'NOP':
                continue
            destinations = []
            is_scalar = key == 'scalar'
            if item['output'] != 31 and item['vector_result'] != is_scalar:
                destinations.append('o' + str(item['output']))
            if item[key + '_temp'] != 63:
                destinations.append(('a' if op == 'ARL' else 'r') + str(item[key + '_temp']))
            if op == 'BRI':
                ops.append(f'BRI {item["branch_target"]} if mask={item["condition_mask"]}')
            else:
                used = (2,) if is_scalar else VECTOR_SOURCES.get(op, ())
                ops.append(op + ' ' + '='.join(destinations) + '.' + item[key + '_mask']
                           + ', ' + ', '.join(sources[i] for i in used))
        cond = item['condition_mask'] if item['condition_test'] else 7
        lines.append(f'{item["slot"]:03}: ' + ' | '.join(ops)
                     + f' ; pred={cond} cc{item["condition_register"]}.{item["condition_swizzle"]}'
                     + (' set_cc' if item['condition_update'] else ''))
    return '\n'.join(lines)
