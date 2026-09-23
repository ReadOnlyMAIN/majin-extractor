#!/usr/bin/env python3
"""
Extracteur universel .pak (format \x00kap, Game Republic PS3) - Version optimisée/multithread
Compatible tex_*.pak, chr*.pak, etc.
Usage:
  python pak_extractor_optimized.py fichier.pak --out DECOMPRESSED/
"""

import zlib
import argparse
import struct
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial


# Constantes pré-compilées pour éviter les recréations
NULL_8 = b'\x00' * 8
NULL_BYTE = b'\x00'


def safe_relpath(name: str) -> Path:
    """Nettoie un chemin pour éviter les problèmes de .. ou de chemins absolus."""
    parts = []
    for part in Path(name).parts:
        if part in ("", ".", ".."):
            continue
        parts.append(part)
    return Path(*parts)


def find_separators_optimized(data, start):
    """
    Trouve les séparateurs (zones de zéros) dans les données.
    Optimisé avec memoryview pour éviter les copies de slices.
    """
    seps = []
    i = start
    end = len(data)
    mv = memoryview(data)
    
    while i < end - 16:
        # Vérification rapide avec memoryview
        if mv[i:i+8] == NULL_8:
            s = i
            # Compter les zéros consécutifs
            while i < end and mv[i] == 0:
                i += 1
            if i - s >= 16:
                seps.append((s, i))
        else:
            i += 1
    return seps


def try_deflate(chunk):
    """
    Tentative de décompression zlib avec wbits=-15 (raw deflate).
    Retourne None en cas d'échec.
    """
    try:
        d = zlib.decompressobj(-15)
        # Estimation conservative de la taille décompressée
        out = d.decompress(chunk, len(chunk) * 50)
        out += d.flush()
        return out
    except Exception:
        return None


def process_chunk_task(chunk_data, idx):
    """
    Tâche de décompression pour un chunk donné.
    Teste avec et sans footer (-16 octets).
    Retourne (idx, result) si succès, None sinon.
    """
    # Tester avec footer (-16)
    if len(chunk_data) >= 16:
        chunk = chunk_data[:-16]
        if len(chunk) >= 8:
            result = try_deflate(chunk)
            if result and len(result) > 16:
                return (idx, result)
    
    # Tester sans footer
    if len(chunk_data) >= 8:
        result = try_deflate(chunk_data)
        if result and len(result) > 16:
            return (idx, result)
    
    return None


def write_file_task(out_dir, names, idx, result):
    """
    Tâche d'écriture pour un fichier décompressé.
    Retourne les infos pour le logging.
    """
    name = names[idx] if idx < len(names) else f"unknown_{idx:004d}"
    magic = result[:4].hex()
    
    rel = safe_relpath(name)
    out_path = out_dir / rel
    
    # Création du dossier (thread-safe avec verrou si nécessaire)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, "wb") as f:
        f.write(result)
    
    return (idx, name, len(result), magic)


def extract_pak(pak_path, out_dir):
    """
    Extraction optimisée d'un fichier PAK avec multithreading pour la décompression.
    """
    # Lecture complète du fichier (nécessaire pour le parsing)
    data = open(pak_path, 'rb').read()
    data_mv = memoryview(data)  # Pour les accès optimisés
    print(f"Pak: {pak_path.name} ({len(data)} bytes)")

    # ===== PARSING HEADER =====
    # Header: magic(4) + version(4) + count(4) + unk(4)
    count = struct.unpack_from('>I', data, 8)[0]
    main_index_end = 0x10 + count * 0x180
    print(f"Count header: {count}  Index principal: 0x{main_index_end:X}")

    # Zone de recherche limitée pour éviter de scanner tout le fichier
    search_limit = min(main_index_end * 4, len(data) // 2)
    
    # ===== RECHERCHE DES ENTRÉES KB/ (1 seule passe au lieu de 2) =====
    all_entries = []
    last_kb_in_index = 0
    pos = 0
    
    # Pré-compiler les offsets pour éviter les recalculs
    while pos < search_limit:
        kb_pos = data.find(b'KB/', pos, search_limit)
        if kb_pos == -1:
            break
        try:
            # Recherche du terminateur null
            end = data.index(NULL_BYTE, kb_pos, kb_pos + 256)
            name_candidate = data[kb_pos:end].decode("ascii", errors="replace")
            
            # Validation du nom (ASCII printable uniquement)
            if len(name_candidate) > 2:
                is_valid = True
                for c in name_candidate:
                    if not (32 <= ord(c) < 127):
                        is_valid = False
                        break
                
                if is_valid:
                    # Lecture des métadonnées (size et block_offset)
                    size = int.from_bytes(data[kb_pos-0x20:kb_pos-0x1C], 'big')
                    block_offset = int.from_bytes(data[kb_pos-0x1C:kb_pos-0x18], 'big')
                    all_entries.append((name_candidate, size, block_offset))
                    last_kb_in_index = kb_pos
            
            pos = end + 1
        except ValueError:
            pos += 3
    
    # Filtrer les entrées avec size > 0
    valid_entries = [(n, s, bo) for n, s, bo in all_entries if s > 0]
    names = [n for n, s, bo in valid_entries]
    
    real_index_end = last_kb_in_index + 0x180
    print(f"Noms valides: {len(names)}  Index reel end: 0x{real_index_end:X}")

    # ===== RECHERCHE DES SÉPARATEURS =====
    seps = find_separators_optimized(data, real_index_end)
    print(f"Separateurs: {len(seps)}")

    if not seps:
        print("Aucun separateur trouve !")
        return 0

    # ===== CONSTRUCTION DES CHUNKS =====
    chunk_starts = [real_index_end] + [s[1] for s in seps]
    chunk_ends = [s[0] for s in seps] + [len(data)]
    
    # Préparer les tâches de décompression
    chunk_tasks = []
    for idx, (cs, ce) in enumerate(zip(chunk_starts, chunk_ends)):
        chunk_data = data[cs:ce]  # Slice nécessaire pour try_deflate
        chunk_tasks.append((idx, chunk_data))

    # ===== DÉCOMPRESSION PARALLÉLISÉE =====
    print(f"Lancement de la décompression parallélisée ({len(chunk_tasks)} chunks)...")
    valid_results = [None] * len(chunk_tasks)  # Pour préserver l'ordre
    
    with ThreadPoolExecutor() as executor:
        # Soumettre toutes les tâches
        futures = {
            executor.submit(process_chunk_task, chunk_data, idx): idx 
            for idx, chunk_data in chunk_tasks
        }
        
        # Collecter les résultats au fur et à mesure
        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            if result:
                _, decompressed = result
                valid_results[idx] = decompressed
    
    # Filtrer les résultats valides (en préservant l'ordre)
    valid_chunks = []
    for idx, result in enumerate(valid_results):
        if result is not None:
            valid_chunks.append((chunk_starts[idx], chunk_ends[idx], result))
    
    print(f"Chunks valides: {len(valid_chunks)}")

    # ===== ÉCRITURE PARALLÉLISÉE DES FICHIERS =====
    ok = 0
    fail = 0
    
    if valid_chunks:
        # Préparer les tâches d'écriture
        write_tasks = []
        for idx, (cs, ce, result) in enumerate(valid_chunks):
            write_tasks.append((idx, result))
        
        # Verrou pour les messages de log (optionnel, mais garde l'ordre)
        print_lock = threading.Lock()
        
        with ThreadPoolExecutor() as executor:
            futures = []
            for idx, result in write_tasks:
                future = executor.submit(
                    write_file_task, out_dir, names, idx, result
                )
                futures.append(future)
            
            # Traiter les résultats au fur et à mesure
            for future in as_completed(futures):
                idx, name, size, magic = future.result()
                with print_lock:
                    print(f"[OK] [{idx}] {name}  ({size} bytes) magic={magic}")
                ok += 1
    
    # ===== GESTION DES ÉCHECS =====
    for name in names[len(valid_chunks):]:
        print(f"[FAIL] {name}: pas de chunk correspondant")
        fail += 1

    print(f"\n{ok} OK  {fail} non mappes")
    return ok


def main():
    """
    Point d'entrée principal. Gère le traitement de plusieurs fichiers ou d'un dossier.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument('pak')
    ap.add_argument('--out', default='DECOMPRESSED')
    args = ap.parse_args()
    
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)
    pak_path = Path(args.pak)

    if pak_path.is_dir():
        # Trouver tous les fichiers .pak récursivement
        pak_files = list(pak_path.rglob("*.pak"))
        print(f"{len(pak_files)} pak trouves\n")

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
