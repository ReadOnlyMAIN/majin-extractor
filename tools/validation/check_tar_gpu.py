#!/usr/bin/env python3
"""Compare tar_shader_gpu.gd captures to the independently RSX-validated equations."""
from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tools.research.tar_shader_math import shade_tar


def expected(sample):
    p=(.03125,-.03125,0)
    points=[(1,1,3),(-1,0,2),(0,1,1)]
    inputs={'COL0':(127/255,1,1,1),'TEX0':(.5,.5,0,0),
            'TEX1':(-p[0]*100,-p[1]*100,200,1),
            'TEX2':(p[0]*100,p[1]*100,-200,0),
            'TEX3':(0,0,1,0),'TEX4':(.3,.4,1,0),'WPOS':(16.5,16.5,0,1)}
    for i,point in enumerate(points):inputs['TEX'+str(5+i)]=(*(100*(a-b) for a,b in zip(point,p)),0)
    uniforms={'tarHP':(1,.4,5,0),'matParam0':(16,.01,.03,.01),
              'matParam1':(.5,0,1,1),'matParam2':(0,.1,.1,0),
              'tarColor':(.2,.3,.4,.6),'colorSky':(.06,.07,.08,1),
              'colorGround':(.01,.02,.03,1),'colorDirLight':(.2,.18,.15,1),
              'specularColor':(.03,.02,.01,1),'envmapColor':(0,0,0,1-sample['cube']),
              'fogParam':(.002,500,.002,0),'fogSelect':tuple(float(i==sample['fog']) for i in range(4)),
              'fogColor':(.01,.02,.03,1),'halfLambertParam':(.7,0,0,0)}
    colors=[(.05,.04,.03,1),(.02,.03,.04,1),(.04,.02,.03,1)]
    for i,param in enumerate([(50,500),(20,400),(10,300)]):
        uniforms['pLightColor'+str(i)]=colors[i]
        uniforms['pLightParam'+str(i)]=(0,param[0],0,param[1])
    tex=[(1,1,1,sample['base_alpha']),(64/255,0,0,1),(51/255,0,0,1),
         (128/255,128/255,1,1),(140/255,116/255,250/255,1),
         (1,0,0,1),(0,1,0,1),(128/255,0,0,1)]
    discarded,rgba=shade_tar(inputs,uniforms,lambda unit,coord:tex[unit],
                             point_light_count=sample['lights'],variant=sample['variant'])
    return (0,0,0,0) if discarded else rgba


def srgb(value):
    return 12.92*value if value<=.0031308 else 1.055*value**(1/2.4)-.055


def compare(samples):
    errors=[]
    for sample in samples:
        rgba=expected(sample)
        # Transparent SubViewport stores premultiplied linear color, then sRGB.
        framebuffer=[srgb(max(0,min(1,c*rgba[3]))) for c in rgba[:3]]+[rgba[3]]
        error=max(abs(a-b) for a,b in zip(framebuffer,sample['rgba']))
        errors.append((error,sample,framebuffer))
    errors.sort(key=lambda row:row[0],reverse=True)
    return dict(samples=len(samples),max_error=errors[0][0],tolerance=2/255,
                failures=sum(error>2/255 for error,_,_ in errors),worst=errors[:3])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=compare(json.loads(args.capture.read_text()))
    print(json.dumps(result,indent=2))
    if args.output:args.output.write_text(json.dumps(result,indent=2)+'\n')
    return int(result['failures']>0)

if __name__=='__main__':raise SystemExit(main())
