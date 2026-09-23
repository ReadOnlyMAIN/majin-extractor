#!/usr/bin/env python
"""
Convertit les fichiers par catégorie (gim, chr, static, demo).
Permet une exécution incrémentale.
"""

import os
import subprocess
import time
from pathlib import Path


def run_cmd(cmd):
    """Exécute une commande et retourne si elle a réussi."""
    print(f"  > {cmd}")
    start = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    elapsed = time.time() - start
    
    if result.returncode != 0:
        print(f"  [FAIL] Echec ({elapsed:.1f}s)")
        if result.stderr:
            print(f"    Erreur: {result.stderr[:200]}")
        return False
    
    if result.stdout:
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if line.strip():
                print(f"    {line}")
    
    print(f"  [OK] Succes ({elapsed:.1f}s)")
    return True


def process_category(category, pak_dir, output_dir):
    """Traite une catégorie de fichiers .pak."""
    category_dir = os.path.join(output_dir, category)
    os.makedirs(category_dir, exist_ok=True)
    
    # Trouver les fichiers .pak de cette catégorie
    pak_files = []
    for f in os.listdir(pak_dir):
        if f.startswith(category) and f.endswith('.pak'):
            pak_files.append(os.path.join(pak_dir, f))
    
    if not pak_files:
        print(f"Aucun fichier {category}*.pak trouvé")
        return 0
    
    print(f"\n=== Traitement de {category} (*.pak) ===")
    print(f"Trouvé {len(pak_files)} fichiers")
    
    extracted_count = 0
    
    for pak_file in pak_files:
        filename = os.path.basename(pak_file)
        print(f"\n  Extraction de {filename}...")
        
        cmd = f'python pak_extractor1_fixed.py "{pak_file}" --out "{category_dir}"'
        if run_cmd(cmd):
            extracted_count += 1
    
    # Classifier les fichiers extraits
    print(f"\n  Classification des fichiers extraits...")
    magic_counts = {}
    for root, dirs, files in os.walk(category_dir):
        for f in files:
            filepath = os.path.join(root, f)
            try:
                with open(filepath, 'rb') as file:
                    magic = file.read(4)
                magic_hex = magic.hex()
                magic_counts[magic_hex] = magic_counts.get(magic_hex, 0) + 1
            except:
                pass
    
    print(f"  Répartition des magics :")
    for magic_hex, count in sorted(magic_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"    {magic_hex}: {count} fichiers")
    
    return extracted_count


def convert_xet_in_category(category_dir, output_dir):
    """Convertit tous les fichiers XET dans une catégorie."""
    textures_dir = os.path.join(output_dir, category + "_textures")
    os.makedirs(textures_dir, exist_ok=True)
    
    # Trouver tous les fichiers XET
    xet_files = []
    for root, dirs, files in os.walk(category_dir):
        for f in files:
            filepath = os.path.join(root, f)
            try:
                with open(filepath, 'rb') as file:
                    magic = file.read(4)
                if magic == b'\x00xet':
                    xet_files.append(filepath)
            except:
                pass
    
    if not xet_files:
        print(f"  Aucun fichier XET trouvé dans {category_dir}")
        return 0
    
    print(f"\n  Conversion de {len(xet_files)} fichiers XET...")
    
    converted_count = 0
    for xet_file in xet_files:
        rel_path = os.path.relpath(xet_file, category_dir)
        out_path = os.path.join(textures_dir, os.path.splitext(rel_path)[0] + '.png')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        
        cmd = f'python previous_work/xet_to_png_fixed.py "{xet_file}" --out "{os.path.dirname(out_path)}"'
        if run_cmd(cmd):
            # Renommer le fichier généré
            generated = os.path.join(os.path.dirname(out_path), os.path.basename(xet_file) + '.png')
            if os.path.exists(generated) and generated != out_path:
                os.rename(generated, out_path)
            converted_count += 1
    
    return converted_count


def convert_ddm_in_category(category_dir, output_dir):
    """Convertit tous les fichiers DDM dans une catégorie."""
    models_dir = os.path.join(output_dir, category + "_models")
    os.makedirs(models_dir, exist_ok=True)
    
    # Trouver tous les fichiers DDM
    ddm_files = []
    for root, dirs, files in os.walk(category_dir):
        for f in files:
            filepath = os.path.join(root, f)
            try:
                with open(filepath, 'rb') as file:
                    magic = file.read(4)
                if magic == b'\x00ddm':
                    ddm_files.append(filepath)
            except:
                pass
    
    if not ddm_files:
        print(f"  Aucun fichier DDM trouvé dans {category_dir}")
        return 0
    
    print(f"\n  Conversion de {len(ddm_files)} fichiers DDM...")
    
    converted_count = 0
    for ddm_file in ddm_files:
        rel_path = os.path.relpath(ddm_file, category_dir)
        out_path = os.path.join(models_dir, os.path.splitext(rel_path)[0] + '_auto.obj')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        
        cmd = f'python previous_work/ddm_decoder.py "{ddm_file}" --out "{os.path.dirname(out_path)}"'
        if run_cmd(cmd):
            # Renommer le fichier généré
            generated = os.path.join(os.path.dirname(out_path), os.path.basename(ddm_file) + '_auto.obj')
            if os.path.exists(generated) and generated != out_path:
                os.rename(generated, out_path)
            converted_count += 1
    
    return converted_count


def main():
    # Configuration
    pak_dir = "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/"
    output_dir = "DECOMPRESSED_BY_CATEGORY"
    
    print("=" * 70)
    print("Conversion des ressources du jeu Majin par catégorie")
    print("=" * 70)
    
    # Catégories à traiter
    categories = [
        # "demo",  # Si le dossier demo existe
        "static",
        "chr",
        "gim",
    ]
    
    total_extracted = 0
    total_xet = 0
    total_ddm = 0
    
    for category in categories:
        print(f"\n{'=' * 70}")
        print(f"Catégorie : {category}")
        print("=" * 70)
        
        # Étape 1 : Extraire
        extracted = process_category(category, pak_dir, output_dir)
        total_extracted += extracted
        
        # Étape 2 : Convertir XET
        xet_converted = convert_xet_in_category(
            os.path.join(output_dir, category),
            output_dir
        )
        total_xet += xet_converted
        
        # Étape 3 : Convertir DDM
        ddm_converted = convert_ddm_in_category(
            os.path.join(output_dir, category),
            output_dir
        )
        total_ddm += ddm_converted
    
    print(f"\n{'=' * 70}")
    print("Résumé")
    print("=" * 70)
    print(f"Fichiers extraits : {total_extracted}")
    print(f"Textures XET converties : {total_xet}")
    print(f"Modèles DDM convertis : {total_ddm}")
    print(f"\nSortie dans : {output_dir}")
    print("=" * 70)


if __name__ == '__main__':
    main()
