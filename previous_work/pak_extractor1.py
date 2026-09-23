#!/usr/bin/env python3
"""
Extracteur universel .pak (format \x00kap, Game Republic PS3)
Compatible tex_*.pak, chr*.pak, etc.
Usage:
  python pak_extractor.py fichier.pak --out DECOMPRESSED/
"""
import zlib, argparse, struct
from pathlib import Path
from collections import defaultdict

def parse_entries(data, index_end):
    """Parser les entrées KB/ UNIQUEMENT dans la zone d'index."""
    entries = []
    pos = 0
    while pos < index_end:
        kb_pos = data.find(b'KB/', pos, index_end)
        if kb_pos == -1: break
        try:
            end = data.index(b'\x00', kb_pos, index_end + 256)
        except ValueError: break
        name = data[kb_pos:end].decode("ascii", errors="replace")
        size         = int.from_bytes(data[kb_pos-0x20:kb_pos-0x1C], 'big')
        block_offset = int.from_bytes(data[kb_pos-0x1C:kb_pos-0x18], 'big')
        entries.append((name, size, block_offset))
        pos = end + 1
    return entries

def find_separators(data, start):
    seps = []
    i = start
    while i < len(data) - 16:
        if data[i:i+8] == b'\x00' * 8:
            s = i
            while i < len(data) and data[i] == 0:
                i += 1
            if i - s >= 16:
                seps.append((s, i))
        else:
            i += 1
    return seps

def try_deflate(chunk):
    try:
        d = zlib.decompressobj(-15)
        out = d.decompress(chunk, len(chunk) * 50)
        out += d.flush()
        return out
    except Exception as e:
        print(f"  [deflate error] {e}")
        return None

def extract_pak(pak_path, out_dir):
    data = open(pak_path, 'rb').read()
    print(f"Pak: {pak_path.name} ({len(data)} bytes)")

    # Header: magic(4) + version(4) + count(4) + unk(4)
    count = struct.unpack_from('>I', data, 8)[0]
    
    # Index principal = 0x10 + count * 0x180
    main_index_end = 0x10 + count * 0x180
    print(f"Count header: {count}  Index principal: 0x{main_index_end:X}")

    # Parser les entrées dans la zone d'index principale
    # + chercher les entrées secondaires juste après
    # (certains paks ont un index secondaire entre main_index_end et les données)
    
    # Trouver tous les KB/ dans une zone raisonnable (2x l'index théorique max)
    search_limit = min(main_index_end * 4, len(data) // 2)
    
    all_entries = []
    pos = 0
    while pos < search_limit:
        kb_pos = data.find(b'KB/', pos, search_limit)
        if kb_pos == -1: break
        try:
            end = data.index(b'\x00', kb_pos, kb_pos + 256)
        except ValueError:
            pos += 3
            continue
        name = data[kb_pos:end].decode("ascii", errors="replace")
        # Vérifier que c'est un nom valide (que des ASCII printables)
        if all(32 <= ord(c) < 127 for c in name) and len(name) > 2:
            size         = int.from_bytes(data[kb_pos-0x20:kb_pos-0x1C], 'big')
            block_offset = int.from_bytes(data[kb_pos-0x1C:kb_pos-0x18], 'big')
            all_entries.append((name, size, block_offset))
        pos = end + 1

    # Garder seulement les entrées avec size > 0
    valid_entries = [(n, s, bo) for n, s, bo in all_entries if s > 0]
    names = [n for n, s, bo in valid_entries]
    
    # Fin réelle de l'index = position du dernier KB/ dans la zone d'index
    # On trouve le dernier kb_pos dans la zone search_limit
    last_kb_in_index = 0
    pos = 0
    while pos < search_limit:
        kb_pos = data.find(b'KB/', pos, search_limit)
        if kb_pos == -1: break
        try:
            end = data.index(b'\x00', kb_pos, kb_pos + 256)
            name_candidate = data[kb_pos:end].decode("ascii", errors="replace")
            if all(32 <= ord(c) < 127 for c in name_candidate):
                last_kb_in_index = kb_pos
            pos = end + 1
        except ValueError:
            pos += 3
    
    real_index_end = last_kb_in_index + 0x180
    print(f"Noms valides: {len(names)}  Index réel end: 0x{real_index_end:X}")

    # Trouver les séparateurs DEPUIS la fin de l'index
    seps = find_separators(data, real_index_end)
    print(f"Séparateurs: {len(seps)}")

    if not seps:
        print("Aucun séparateur trouvé !")
        return 0

    # Construire les chunks
    chunk_starts = [real_index_end] + [s[1] for s in seps]
    chunk_ends   = [s[0] for s in seps] + [len(data)]

    # Tester chaque chunk
    valid_chunks = []
    for cs, ce in zip(chunk_starts, chunk_ends):
        # Essai avec footer (-16)
        chunk = data[cs : ce - 16]
        if len(chunk) >= 8:
            result = try_deflate(chunk)
            if result and len(result) > 16:
                valid_chunks.append((cs, ce, result))
                continue
        # Essai sans footer
        chunk2 = data[cs:ce]
        if len(chunk2) >= 8:
            result2 = try_deflate(chunk2)
            if result2 and len(result2) > 16:
                valid_chunks.append((cs, ce, result2))

    print(f"Chunks valides: {len(valid_chunks)}")

    ok = fail = 0
    for idx, (cs, ce, result) in enumerate(valid_chunks):
        name = names[idx] if idx < len(names) else f"unknown_{idx:04d}"
        magic = result[:4].hex()
        def safe_relpath(name: str) -> Path:
            parts = []
            for part in Path(name).parts:
                if part in ("", ".", ".."):
                    continue
                parts.append(part)
            return Path(*parts)

        rel = safe_relpath(name)
        out_path = out_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "wb") as f:
            f.write(result)

        print(f"✓ [{idx}] {name}  ({len(result)} bytes) magic={magic}")
        ok += 1

    for name in names[len(valid_chunks):]:
        print(f"✗ {name}: pas de chunk correspondant")
        fail += 1

    print(f"\n✓ {ok} OK  ✗ {fail} non mappés")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pak')
    ap.add_argument('--out', default='DECOMPRESSED')
    args = ap.parse_args()
    out_dir = Path(args.out); out_dir.mkdir(exist_ok=True)
    pak_path = Path(args.pak)

    if pak_path.is_dir():
        pak_files = list(pak_path.rglob("*.pak"))

        print(f"{len(pak_files)} pak trouvés\n")

        total = 0
        for pak in pak_files:
            try:
                print(f"\n=== {pak.name} ===")
                total += extract_pak(pak, out_dir)
            except Exception as e:
                print(f"Erreur {pak.name}: {e}")

        print(f"\nTOTAL EXTRAITS: {total}")

    else:
        extract_pak(pak_path, out_dir)

if __name__ == '__main__':
    main()