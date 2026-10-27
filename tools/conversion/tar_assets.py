"""Install original global textures used by KbTarModel from local game resources."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

NAMES = ('waterwave_n2', 'AmbientSphere3', 'noise')


def configure_vector_texture_import(path: Path) -> Path:
    """Keep the source normal's three channels; BC5 would discard source blue."""
    import re
    sidecar = Path(str(path) + '.import')
    content = sidecar.read_text() if sidecar.exists() else '[remap]\nimporter="texture"\ntype="CompressedTexture2D"\n\n[params]\n'
    if '[params]' not in content:
        content += '\n[params]\n'
    settings = {'compress/mode': '0', 'compress/normal_map': '2',
                'mipmaps/generate': 'true', 'detect_3d/compress_to': '0'}
    for key,value in settings.items():
        pattern = r'^' + re.escape(key) + r'=.*$'
        if re.search(pattern,content,re.M):
            content = re.sub(pattern,key+'='+value,content,flags=re.M)
        else:
            content = content.replace('[params]', '[params]\n'+key+'='+value,1)
    sidecar.write_text(content)
    return sidecar


def write_tar_globals(kb_root: Path, output_root: Path, static_pak: Path | None = None) -> dict:
    """Resolve exact resource names; never substitute a texture by resemblance."""
    try:
        from .xet_to_png import convert_one
    except ImportError:
        from xet_to_png import convert_one
    import sys
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.extraction.pak_extractor import indexed_resources
    payloads = {name: (kb_root / 'texture' / name).read_bytes()
                for name in NAMES if (kb_root / 'texture' / name).is_file()}
    if len(payloads) != len(NAMES) and static_pak and static_pak.is_file():
        for name, _, payload in indexed_resources(static_pak.read_bytes()):
            for texture in NAMES:
                if name == 'KB/texture/' + texture:
                    payloads.setdefault(texture, payload)
    missing = sorted(set(NAMES) - payloads.keys())
    if missing:
        raise FileNotFoundError('Original tar globals missing: ' + ', '.join(missing))
    target = output_root / 'tar_globals'
    target.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory() as directory:
        for name in NAMES:
            path = Path(directory) / name
            path.write_bytes(payloads[name])
            if not convert_one(path, target):
                raise ValueError('Invalid original tar texture: ' + name)
            configure_vector_texture_import(target / (name + '.png'))
            records.append(dict(resource='KB/texture/' + name, png=name + '.png',
                                sha256=hashlib.sha256(payloads[name]).hexdigest()))
    report = dict(textures=records, evidence={
        'waterwave_n2': 'EBOOT VA 0x1ca68 stores context+0xd34; VA 0x43b730 binds it to context+0xb94 (textureSamplerTar)',
        'AmbientSphere3': 'EBOOT VA 0x1cae4 stores context+0xd40; VA 0x43b948 binds textureSamplerAmbient in material cases 2/3',
        'noise': 'EBOOT VA 0x1cb40 stores context+0xd4c; VA 0x43b840 binds textureSamplerAmbient conditionally in material cases 4/5',
    })
    (target / 'bindings.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kb-root', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--static-pak', type=Path)
    args = parser.parse_args()
    write_tar_globals(args.kb_root, args.output_root, args.static_pak)


if __name__ == '__main__':
    main()
