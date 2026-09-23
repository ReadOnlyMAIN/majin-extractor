#!/usr/bin/env python3
"""
Convertit les .dds (DXT1/DXT5) directement en PNG
Usage:
  python dds_to_png.py DOSSIER_DDS/ --out DOSSIER_PNG/
"""
import struct, argparse
from pathlib import Path
from PIL import Image

def rgb565(c):
    return (((c>>11)&31)*255//31, ((c>>5)&63)*255//63, (c&31)*255//31)

def decode_alpha_table(a0, a1):
    t = [a0, a1, 0, 0, 0, 0, 0, 0]
    if a0 > a1:
        t[2]=(6*a0+1*a1)//7; t[3]=(5*a0+2*a1)//7
        t[4]=(4*a0+3*a1)//7; t[5]=(3*a0+4*a1)//7
        t[6]=(2*a0+5*a1)//7; t[7]=(1*a0+6*a1)//7
    else:
        t[2]=(4*a0+1*a1)//5; t[3]=(3*a0+2*a1)//5
        t[4]=(2*a0+3*a1)//5; t[5]=(1*a0+4*a1)//5
        t[6]=0; t[7]=255
    return t

def decode_dxt1(raw, w, h):
    bw=(w+3)//4; bh=(h+3)//4
    raw=(raw+b'\x00'*(bw*bh*8))[:bw*bh*8]
    out=bytearray(w*h*4)
    for by in range(bh):
        for bx in range(bw):
            i=(by*bw+bx)*8
            c0=struct.unpack_from('<H',raw,i)[0]
            c1=struct.unpack_from('<H',raw,i+2)[0]
            bits=struct.unpack_from('<I',raw,i+4)[0]
            p=[rgb565(c0),rgb565(c1)]
            if c0>c1:
                p.append(tuple((2*p[0][j]+p[1][j])//3 for j in range(3)))
                p.append(tuple((p[0][j]+2*p[1][j])//3 for j in range(3)))
            else:
                p.append(tuple((p[0][j]+p[1][j])//2 for j in range(3)))
                p.append((0,0,0))
            for py in range(4):
                for px in range(4):
                    idx=(bits>>(2*(4*py+px)))&3
                    x,y=bx*4+px,by*4+py
                    if x<w and y<h:
                        o=(y*w+x)*4
                        r,g,b=p[idx]
                        out[o:o+4]=bytes((r,g,b,255))
    return bytes(out)

def decode_dxt5(raw, w, h):
    bw=(w+3)//4; bh=(h+3)//4
    raw=(raw+b'\x00'*(bw*bh*16))[:bw*bh*16]
    out=bytearray(w*h*4)
    for by in range(bh):
        for bx in range(bw):
            i=(by*bw+bx)*16
            block=raw[i:i+16]
            a0,a1=block[0],block[1]
            abits=int.from_bytes(block[2:8],'little')
            alpha=decode_alpha_table(a0,a1)
            c0=struct.unpack_from('<H',block,8)[0]
            c1=struct.unpack_from('<H',block,10)[0]
            bits=struct.unpack_from('<I',block,12)[0]
            p=[rgb565(c0),rgb565(c1)]
            if c0>c1:
                p.append(tuple((2*p[0][j]+p[1][j])//3 for j in range(3)))
                p.append(tuple((p[0][j]+2*p[1][j])//3 for j in range(3)))
            else:
                p.append(tuple((p[0][j]+p[1][j])//2 for j in range(3)))
                p.append((0,0,0))
            for py in range(4):
                for px in range(4):
                    idx=py*4+px
                    ci=(bits>>(2*idx))&3
                    ai=(abits>>(3*idx))&7
                    x,y=bx*4+px,by*4+py
                    if x<w and y<h:
                        o=(y*w+x)*4
                        r,g,b=p[ci]
                        out[o:o+4]=bytes((r,g,b,alpha[ai]))
    return bytes(out)

def convert_dds(path, out_dir):
    data = Path(path).read_bytes()
    if data[:4] != b'DDS ':
        print(f"[skip] {Path(path).name}")
        return False

    # Parser le header DDS (128 bytes)
    h      = struct.unpack_from('<I', data, 12)[0]
    w      = struct.unpack_from('<I', data, 16)[0]
    fourcc = data[84:88]

    if w == 0 or h == 0:
        return False

    # Données après le header de 128 bytes
    pixel_data = data[128:]

    if fourcc == b'DXT1':
        rgba = decode_dxt1(pixel_data, w, h)
    elif fourcc == b'DXT5':
        rgba = decode_dxt5(pixel_data, w, h)
    else:
        print(f"[skip] {Path(path).name}: format {fourcc} non supporté")
        return False

    img = Image.frombytes('RGBA', (w,h), rgba)
    bg  = Image.new('RGBA', (w,h), (204,204,204,255))
    Image.alpha_composite(bg, img).save(out_dir / (Path(path).stem + '.png'))
    print(f"[OK] {Path(path).name} ({w}x{h} {fourcc.decode()})")
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--out', default='PNG_OUT')
    args = ap.parse_args()
    inp = Path(args.input); out_dir = Path(args.out); out_dir.mkdir(exist_ok=True)
    if inp.is_dir():
        files = sorted(list(inp.glob('*.dds')) + list(inp.glob('*.bin')))
        ok = sum(1 for f in files if convert_dds(f, out_dir))
        print(f"\n{ok}/{len(files)} convertis dans {out_dir}/")
    else:
        convert_dds(inp, out_dir)

if __name__ == '__main__':
    main()