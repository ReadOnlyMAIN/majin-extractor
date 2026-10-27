#!/usr/bin/env python3
"""Record bounded KbTarModel host evidence from a local decrypted Majin ELF.

Addresses below identify the examined disc build. This is not a general-purpose
PPU decompiler, and unrelated builds are rejected by instruction checks.
The original executable and decryption keys are never included in the report.
"""
from __future__ import annotations
import argparse,hashlib,json,struct
from pathlib import Path


class ElfMemory:
    def __init__(self,data: bytes):
        if data[:6] != b'\x7fELF\x02\x02':
            raise ValueError('requires a decrypted big-endian ELF64 executable')
        self.data=data
        self.entry=struct.unpack_from('>Q',data,24)[0]
        table=struct.unpack_from('>Q',data,32)[0]
        stride,count=struct.unpack_from('>HH',data,54)
        self.segments=[]
        for i in range(count):
            kind,flags,offset,address,physical,size,memory,align=struct.unpack_from('>II6Q',data,table+i*stride)
            if kind==1 and size:self.segments.append((address,offset,size))

    def read(self,address,size=4):
        for base,offset,length in self.segments:
            if base<=address and address+size<=base+length:
                value=self.data[offset+address-base:offset+address-base+size]
                if len(value)!=size:break
                return value
        raise ValueError(f'unmapped/truncated ELF address 0x{address:x}')

    def word(self,address):return struct.unpack('>I',self.read(address))[0]

    def toc_float(self,pc,toc):
        word=self.word(pc)
        if word>>26 not in (32,48) or (word>>16)&31!=2:
            raise ValueError(f'expected TOC float load at 0x{pc:x}; different build')
        offset=struct.unpack('>h',struct.pack('>H',word&65535))[0]
        return struct.unpack('>f',self.read(toc+offset))[0]


def inspect(data):
    memory=ElfMemory(data)
    toc=memory.word(memory.entry+4)
    evidence={}
    for label,pc in {'flow_rate':0x4ead0,'flow_angle_degrees':0x4eaf8,
                     'second_flow_rate':0x4eb0c,'second_flow_angle_degrees':0x4eb14,
                     'transition_feather':0x9437c,'transition_slope':0x94390,
                     'tar_phong_exponent_branch0':0x94580,'tar_edge_specular_branch0':0x94584}.items():
        evidence[label]=dict(value=memory.toc_float(pc,toc),ppu_address=f'0x{pc:x}')
    bindings=[]
    for texture,sampler,pc,slot,handle in [
        ('waterwave_n2','textureSamplerTar',0x43b730,0xd34,0xb94),
        ('AmbientSphere3','textureSamplerAmbient',0x43b948,0xd40,0xb9c),
        ('noise','textureSamplerAmbient',0x43b840,0xd4c,0xb9c),
    ]:
        if memory.word(pc)>>26!=32 or memory.word(pc)&65535!=slot or memory.word(pc+4)&65535!=handle:
            raise ValueError(f'unexpected texture binding at 0x{pc:x}; different build')
        if ('KB/texture/'+texture).encode()+b'\0' not in data:
            raise ValueError('missing original texture identifier')
        bindings.append(dict(resource='KB/texture/'+texture,sampler=sampler,
                             ppu_address=f'0x{pc:x}',conditional=texture!='waterwave_n2'))
    return dict(elf_sha256=hashlib.sha256(data).hexdigest(),constants=evidence,bindings=bindings,
                limitations=['flow rates are constructor defaults; map/runtime overrides remain possible',
                             'transition feather/slope belong to the engine transition branch; not a proven PV curve',
                             'tar Phong settings have additional material-selector branches',
                             'sampler state and framebuffer/postprocess capture are not inferred'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=inspect(args.elf.read_bytes());text=json.dumps(report,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text,end='')

if __name__=='__main__':main()
