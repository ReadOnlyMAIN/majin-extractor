"""Mathematical reference for the decoded KbTarModel fragment programs.

Not an RSX accuracy emulator: arithmetic uses Python floats; sampler filtering,
texture state, FP16 rounding and fragment interpolation belong to the host.
The instruction evaluator is deliberately independent of the simplified formulas.
"""
from __future__ import annotations
import math


def tar_coverage(mask: float, tar_hp, base_alpha: float = 1.0) -> tuple[bool, float]:
    """Exact branch/alpha equations of KbTarModel variants 0 and 7.

    The mask is interpolated source vertex COLOR.r, clamped on fragment input.
    x is the discard threshold, y the fade start, z the fade slope; w is unused.
    Alpha is not multiplied by base_alpha; that texture only gates discard.
    """
    mask = min(1.0, max(0.0, mask))
    if base_alpha < 0.009999999776482582 or mask > tar_hp[0]:
        return True, 0.0
    return False, 1.0 - (mask - tar_hp[1]) * tar_hp[2] if mask > tar_hp[1] else 1.0


def tar_coordinates(position_relative_to_root, normal, uv_offset=(0, 0, 0, 0)):
    """Exact real-arithmetic polynomial and literals from the tar VS (v0/v7).

    Source position units are preserved (the DDM corpus uses centimeters).
    This returns TEX0.zw, not the mesh's ordinary UV coordinates.
    """
    nx, _, nz = normal
    major, minor = max(abs(nx), abs(nz)), min(abs(nx), abs(nz))
    q = minor / major if major else float('nan')
    q2 = q * q
    angle = (((((-0.01348114013671875 * q2 + 0.057464599609375) * q2
                - 0.1212158203125) * q2 + 0.1956787109375) * q2
              - 0.3330078125) * q2 + 1.0) * q
    if abs(nz) > abs(nx):
        angle = 1.5703125 - angle
    if nx < 0:
        angle = 3.140625 - angle
    if nz < 0:
        angle = -angle
    x, y, z = position_relative_to_root
    u = ((3.1414999961853027 + angle) * 0.31831926107406616
         + (x + z) * 0.012500000186264515 + uv_offset[2])
    v = y * 0.03333333507180214 + uv_offset[3]
    return u, v


def _divide(a, b):
    if b:
        return a / b
    return math.copysign(float('inf'), a) if a else float('nan')


def _rsqrt(x):
    return _divide(1.0, math.sqrt(x)) if x >= 0 else float('nan')


def run_fragment(program: dict, inputs: dict, uniforms: dict, sample, *, trace=False):
    """Evaluate *all* decoded tar FP ops, branches and per-lane predicates.

    sample(unit, coords4) returns RGBA; unit 5 is the engine environment cube.
    Returns (discarded, RGBA), or additionally a per-instruction trace.
    Unwritten temporary lanes start at zero, as in the RPCS3 reference backend.
    This initialization is not a proof of PS3 power-on register contents.
    """
    regs = {f'{kind}{i}': [0.0] * 4 for kind in ('R', 'H') for i in range(48)}
    cc = [[0.0] * 4 for _ in range(2)]
    instructions = program['fragment']
    addresses = {item['slot']: i for i, item in enumerate(instructions)}
    histories = []
    def pred(value, mask):
        return bool(mask & (1 if value < 0 else 2 if value == 0 else 4 if value > 0 else 0))
    def read(source, item):
        kind = source['kind']
        if kind == 'temporary':
            value = regs[('H' if source['fp16'] else 'R') + str(source['register_index'])]
        elif kind == 'input':
            value = inputs.get(item['input_attribute'], [0.0] * 4)
            if item['input_attribute'] in ('COL0', 'COL1'):
                value = [min(1.0, max(0.0, x)) for x in value]
        elif kind == 'constant':
            value = uniforms[item['constant_name']] if item.get('constant_name') else item['literal']
        else:
            raise ValueError('unsupported source type')
        swizzle = source['swizzle'] or 'xyzw'
        if len(swizzle) == 1:
            swizzle *= 4
        value = [value['xyzw'.index(axis)] for axis in swizzle]
        if source['absolute']:
            value = [abs(x) for x in value]
        if source['negate']:
            value = [-x for x in value]
        return value
    pc = 0
    while pc < len(instructions):
        item = instructions[pc]
        op = item['opcode']
        conditions = [pred(cc[item['condition_register']]['xyzw'.index(c)], item['condition_mask'])
                      for c in item['condition_swizzle']]
        if op == 'IFE':
            if item['else_slot'] != item['end_slot']:
                raise ValueError('unexpected if/else shape; do not silently linearize')
            if not any(conditions):
                pc = addresses.get(item['end_slot'], len(instructions))
                continue
        elif op == 'KIL':
            if any(conditions):
                result = (True, tuple(regs['H0']))
                return (*result, histories) if trace else result
        elif op not in ('FENCT', 'FENCB', 'NOP'):
            args = [read(source, item) for source in item['sources'][:item['operand_count']]]
            a = args[0]
            b = args[1] if len(args) > 1 else None
            if op == 'TEX': value = list(sample(item['texture_unit'], a))
            elif op == 'MOV': value = a
            elif op == 'ADD': value = [x+y for x,y in zip(a,b)]
            elif op == 'MUL': value = [x*y for x,y in zip(a,b)]
            elif op == 'MAD': value = [x*y+z for x,y,z in zip(a,b,args[2])]
            elif op in ('DP2','DP3','DP4'): value = [sum(a[i]*b[i] for i in range(int(op[-1])))]*4
            elif op == 'MIN': value = [min(x,y) for x,y in zip(a,b)]
            elif op == 'MAX': value = [max(x,y) for x,y in zip(a,b)]
            elif op == 'SGT': value = [float(x>y) for x,y in zip(a,b)]
            elif op == 'SLT': value = [float(x<y) for x,y in zip(a,b)]
            elif op == 'DIV': value = [_divide(x,b[0]) for x in a]
            elif op == 'DIVSQ': value = [x*_rsqrt(b[0]) for x in a]
            elif op == 'RSQ': value = [_rsqrt(a[0])]*4
            elif op == 'SIN': value = [math.sin(a[0])]*4
            elif op == 'EX2': value = [2.0**a[0]]*4
            elif op == 'LG2': value = [math.log2(a[0]) if a[0]>0 else -float('inf')]*4
            elif op == 'NRM':
                inv = _rsqrt(sum(x*x for x in a[:3]))
                value = [a[i]*inv for i in (0,1,2,2)]
            else: raise ValueError(f'unsupported opcode {op}')
            scale = {'1':1,'2':2,'4':4,'8':8,'1/2':.5,'1/4':.25,'1/8':.125}[item['result_scale']]
            value = [x*scale for x in value]
            if item['saturate']:
                value = [min(1.0,max(0.0,x)) for x in value]
            if item['destination']:
                dest = regs[item['destination'].split('.')[0]]
            else:
                dest = None
            for axis in item['write_mask']:
                lane = 'xyzw'.index(axis)
                if not conditions[lane]:
                    continue
                if dest is not None:
                    dest[lane] = value[lane]
                if item['set_condition']:
                    cc[item['condition_write_register']][lane] = value[lane]
            if trace:
                histories.append(dict(slot=item['slot'], opcode=op, result=value[:], cc=[x[:] for x in cc]))
        pc += 1
    result = (False, tuple(regs['H0']))
    return (*result, histories) if trace else result


def shade_tar(inputs: dict, uniforms: dict, sample, *, point_light_count=0, variant=0):
    """Simplified complete fragment equations of all 32 KbTarModel passes.

    All shader inputs remain explicit: TBN-space view/light/hemisphere vectors,
    engine textures, dynamic uniforms. No guessed game health-to-tarHP curve.
    Real arithmetic, not a bit-exact replacement for RSX FP16/FMA behavior.
    """
    def dot(a,b): return sum(x*y for x,y in zip(a,b))
    def norm(a):
        length = math.sqrt(dot(a,a))
        return tuple(x/length for x in a)
    uv = inputs['TEX0']
    mp0, mp1, mp2 = (uniforms[k] for k in ('matParam0','matParam1','matParam2'))
    base_sample = sample(0,uv)
    base_alpha = base_sample[3]
    if variant in (0, 7):
        discarded, alpha = tar_coverage(inputs['COL0'][0], uniforms['tarHP'], base_alpha)
    else:
        discarded = base_alpha < 0.009999999776482582 if variant not in (2, 5) else False
        alpha = uniforms['tarColor'][3]
    if discarded:
        return True, (0.0,0.0,0.0,0.0)
    tar_normal = norm(tuple(2*x-1 for x in sample(4,(uv[2],uv[3],uv[2],uv[2]))[:3]))
    base_normal = norm(tuple(2*x-1 for x in sample(3,uv)[:3]))
    noise_base = sample(2,(uv[0]+mp2[1],uv[1],0,0))[0]
    noise_tar = sample(2,(uv[2]+mp2[1],uv[3],0,0))[0]
    amp_base = mp1[0]+mp2[2]*math.sin(6.2829999923706055*noise_base)
    amp_tar = .5+mp2[2]*math.sin(6.2829999923706055*noise_tar)
    bxy = tuple(x*amp_base for x in base_normal[:2])
    txy = tuple(x*amp_tar for x in tar_normal[:2])
    b = norm((*bxy, math.sqrt(1.0000100135803223-dot(bxy,bxy))))
    t = norm((*txy, math.sqrt(1.0-dot(txy,txy))))
    tar_weight = base_alpha if variant in (2, 5) else 1.0
    n = norm(tuple(x+tar_weight*y for x,y in zip(b,t)))
    v, hemi, light = (norm(inputs[k][:3]) for k in ('TEX2','TEX3','TEX4'))
    half = norm(tuple(l-a for l,a in zip(light,v)))
    shadow_uv = (inputs['WPOS'][0]*0.0007812500116415322+0.0007812500116415322,
                 inputs['WPOS'][1]*0.0013888889225199819+0.0013888999819755554,0,0)
    shadow = sample(7,shadow_uv)[0]
    direct = max(dot(n,light),0.0)*shadow
    hemisphere = .5*dot(n,hemi)+.5
    lighting = tuple(hemisphere*(sky-ground)+ground+direct*sun for sky,ground,sun in zip(
        uniforms['colorSky'][:3],uniforms['colorGround'][:3],uniforms['colorDirLight'][:3]))
    point_diffuse = [0.0]*3
    point_phong = []
    for index in range(point_light_count):
        direction = inputs['TEX'+str(5+index)][:3]
        length = math.sqrt(dot(direction,direction))
        point_light = norm(direction)
        point_param = uniforms['pLightParam'+str(index)]
        attenuation = min(1.0, max(0.0, (point_param[3]-length+point_param[1])/point_param[3]))
        diffuse = max(dot(n,point_light),0.0)*attenuation
        point_half = norm(tuple(l-a for l,a in zip(point_light,v)))
        point_phong.append(max(dot(n,point_half),0.0)**mp0[0]*diffuse)
        for channel in range(3):
            point_diffuse[channel] += uniforms['pLightColor'+str(index)][channel]*diffuse
    lighting = tuple(a+b for a,b in zip(lighting,point_diffuse))
    fresnel = 1.0-abs(dot(n,v))
    environment_strength = mp0[1]+(mp0[2]-mp0[1])*fresnel
    phong = max(dot(n,half),0.0)**mp0[0]*direct
    phong = max([phong, *point_phong])
    specular = sample(1,uv)[0]*phong+mp0[3]*fresnel**3*max(dot(v,light),0.0)**2
    r = tuple(2*dot(n,v)*x-y for x,y in zip(n,v))
    if uniforms['envmapColor'][3] < 0.009999999776482582:
        env = sample(5,(*r,0))[:3]
    else:
        reflected = tuple(-x for x in r)
        denominator = 2*math.sqrt(reflected[0]**2+reflected[1]**2+(reflected[2]+1)**2)
        env = sample(6,(reflected[0]/denominator+.5,-reflected[1]/denominator+.5,0,0))[:3]
    utility = sample(1,uv)[0]
    albedo = uniforms['tarColor'][:3]
    if variant in (2, 5):
        albedo = tuple(base+base_alpha*(tar-base) for base,tar in zip(base_sample[:3],albedo))
    color = tuple(tar*lit+ks*specular+tar_weight*utility*environment_strength*environment
                  for tar,lit,ks,environment in zip(albedo,lighting,
                                                    uniforms['specularColor'][:3],env))
    distance = math.sqrt(dot(inputs['TEX1'][:3],inputs['TEX1'][:3]))
    fog = uniforms['fogParam']
    # 1.442695 is the literal conversion used by the bytecode, not an exact log2(e).
    log2e = 1.4426950216293335
    candidates = ((fog[1]-distance)*fog[0], 2**(-fog[2]*distance*log2e),
                  2**(-fog[2]**2*distance**2*log2e))
    fog_weight = min(1.0,max(0.0,dot(candidates,uniforms['fogSelect'][:3])))
    color = tuple((fog_weight*c+(1-fog_weight)*f)*uniforms['halfLambertParam'][0]
                  for c,f in zip(color,uniforms['fogColor'][:3]))
    return False, (*color,alpha)


def run_vertex(program: dict, attributes: dict[int, tuple], constants: dict[int, tuple]):
    """Evaluate every VS operation used by all 32 decoded tar passes.

    The constant bank includes bone matrices and frame uniforms supplied by
    the host. Immutable compiler literals are read from the original resource.
    """
    constants = {**program['vertex_defaults'], **constants}
    r = [[0.0]*4 for _ in range(64)]
    out = [[0.0,0.0,0.0,1.0] for _ in range(16)]
    address = [[0]*4 for _ in range(2)]
    cc = [[0.0]*4 for _ in range(2)]
    def predicate(value, mask):
        return bool(mask & (1 if value<0 else 2 if value==0 else 4 if value>0 else 0))
    pc, fuel = 0, 4096
    while pc < len(program['vertex']):
        fuel -= 1
        if not fuel:
            raise ValueError('vertex program did not terminate')
        item = program['vertex'][pc]
        condition_values = [cc[item['condition_register']]['xyzw'.index(c)] for c in item['condition_swizzle']]
        predicate_mask = [predicate(x,item['condition_mask']) for x in condition_values]
        writes = predicate_mask if item['condition_test'] else [True]*4
        args = []
        for source in item['sources']:
            register = source['register']
            if source['kind']=='temporary':
                value = r[register]
            elif source['kind']=='input':
                value = attributes.get(register,(0,0,0,1))
            elif source['kind']=='constant':
                if source['indexed']:
                    register = (register+address[item['address_register']]['xyzw'.index(item['address_component'])])&511
                value = constants[register]
            else:
                raise ValueError('invalid source register type')
            value = [value['xyzw'.index(c)] for c in source['swizzle']]
            if source['absolute']: value = [abs(x) for x in value]
            if source['negate']: value = [-x for x in value]
            args.append(value)
        results = {}
        a,b,c = args
        op = item['vector']
        if op=='MOV': value=a
        elif op=='MUL': value=[x*y for x,y in zip(a,b)]
        elif op=='ADD': value=[x+y for x,y in zip(a,c)]
        elif op=='MAD': value=[x*y+z for x,y,z in zip(a,b,c)]
        elif op in ('DP3','DP4'): value=[sum(a[i]*b[i] for i in range(int(op[-1])))]*4
        elif op=='DPH': value=[sum(a[i]*b[i] for i in range(3))+b[3]]*4
        elif op=='MAX': value=[max(x,y) for x,y in zip(a,b)]
        elif op=='MIN': value=[min(x,y) for x,y in zip(a,b)]
        elif op=='SLT': value=[float(x<y) for x,y in zip(a,b)]
        elif op=='SGT': value=[float(x>y) for x,y in zip(a,b)]
        elif op=='ARL': value=[math.floor(x) for x in a]
        elif op!='NOP': raise ValueError('unsupported vector opcode '+op)
        if op!='NOP': results['vector']=value
        op = item['scalar']
        if op=='MOV': results['scalar']=[c[0]]*4
        elif op=='RCP': results['scalar']=[_divide(1,c[0])]*4
        elif op=='RSQ': results['scalar']=[_rsqrt(c[0])]*4
        elif op not in ('NOP','BRI'): raise ValueError('unsupported scalar opcode '+op)
        for key,value in results.items():
            if item['saturate']: value=[min(1,max(0,x)) for x in value]
            scalar = key=='scalar'
            destinations = []
            if item['output']!=31 and item['vector_result']!=scalar:
                destinations.append(out[item['output']])
            temp = item[key+'_temp']
            if temp!=63:
                destinations.append(address[temp] if item['vector']=='ARL' and not scalar else r[temp])
            for axis in item[key+'_mask']:
                lane = 'xyzw'.index(axis)
                if not writes[lane]: continue
                for target in destinations: target[lane]=value[lane]
                if item['condition_update']: cc[item['condition_register']][lane]=value[lane]
        if item['end']:
            break
        if item['scalar']=='BRI' and any(predicate_mask):
            pc=item['branch_target']
        else:
            pc+=1
    return out


# Compatibility name for the directional-only specialization.
shade_single_light = shade_tar
