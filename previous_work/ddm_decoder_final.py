"""
Decodeur DDM FINAL -> OBJ
========================

Fonctionnalités:
- Détection automatique de toutes les sections géométriques
- Décodage de TOUTES les sections (pas seulement la première)
- Combinaison des vertices et triangles de toutes les sections
- Deduplication des vertices
- Remappage des indices
- Détection automatique de l'endianness
- Stratégie de décodage automatique (strip ou groupes de 6)

Résout les problèmes:
- Trous dans les modèles (sections manquantes)
- Étirements (vertices dupliqués non dédupliqués)
- Modèles incomplets (seulement la première section décodée)
"""

import struct
import math
import sys
import os
from pathlib import Path

MAGIC = b"\x00ddm"


def dist(p1, p2):
    return math.sqrt(sum((p1[k] - p2[k]) ** 2 for k in range(3)))


def find_geometry_headers(data):
    """Cherche tous les headers de section geo (\x00\x00\x00\x07 + nb_verts/nb_sub/nb_idx)."""
    pat = bytes([0, 0, 0, 7])
    pos = 0
    headers = []
    while True:
        pos = data.find(pat, pos)
        if pos == -1:
            break
        if pos + 16 <= len(data):
            # Essayer les deux endianness
            nb_verts_be, nb_sub_be, nb_idx_be = struct.unpack(">III", data[pos + 4 : pos + 16])
            nb_verts_le, nb_sub_le, nb_idx_le = struct.unpack("<III", data[pos + 4 : pos + 16])
            
            # Vérifier lequel donne des valeurs raisonnables
            def is_plausible(nv, ns, ni):
                if nv < 3 or ni < 3:
                    return False
                ratio = ni / nv if nv > 0 else 999
                return ratio < 100
            
            if is_plausible(nb_verts_be, nb_sub_be, nb_idx_be):
                idx_start = pos + 0x14
                if idx_start + nb_idx_be * 2 <= len(data):
                    headers.append((pos, nb_verts_be, nb_sub_be, nb_idx_be, '>'))
            
            if is_plausible(nb_verts_le, nb_sub_le, nb_idx_le):
                idx_start = pos + 0x14
                if idx_start + nb_idx_le * 2 <= len(data):
                    headers.append((pos, nb_verts_le, nb_sub_le, nb_idx_le, '<'))
        pos += 1
    return headers


def find_vertex_buffer(data, search_start, search_end, min_run=4):
    """Cherche le buffer de vertices (marqueur 0xFFFFFFFF à stride 24)."""
    search_end = min(search_end, len(data) - 16)
    if search_end <= search_start:
        return None

    marker = b"\xff\xff\xff\xff"
    best = None
    tried_starts = set()

    pos = search_start + 12
    limit = search_end + 16
    while True:
        hit = data.find(marker, pos, limit)
        if hit == -1:
            break
        candidate_start = hit - 12
        if candidate_start >= search_start and candidate_start not in tried_starts:
            tried_starts.add(candidate_start)
            run = 0
            p = candidate_start
            while p + 16 <= len(data):
                if data[p + 12 : p + 16] == marker:
                    run += 1
                    p += 24
                else:
                    break
            if run >= min_run and (best is None or run > best[1]):
                best = (candidate_start, run)
        pos = hit + 1

    return best


def load_vertices(data, vbuf_start, n, endian='>'):
    """Charge les vertices (positions seulement, 12 octets par vertex)."""
    vertices = []
    for i in range(n):
        base = vbuf_start + i * 24
        if base + 12 > len(data):
            break
        x, y, z = struct.unpack(f"{endian}fff", data[base : base + 12])
        vertices.append((x, y, z))
    return vertices


VIF_TAG2 = b"\x00\x00\x3c\x00"


def _to_signed16(v):
    return v - 65536 if v >= 32768 else v


def try_vif_vertex_buffer(data, vbuf_start, n, scale=16384.0):
    """Charge un buffer de vertices au format VIF/GS compressé."""
    vertices = []
    tag2_matches = 0
    for i in range(n):
        base = vbuf_start + i * 16
        if base + 16 > len(data):
            break
        row = data[base : base + 16]
        if row[8:12] == VIF_TAG2:
            tag2_matches += 1
        a1, a2 = struct.unpack(">HH", row[4:8])
        b1, _ = struct.unpack(">HH", row[12:16])
        x = _to_signed16(a1) / scale
        y = _to_signed16(a2) / scale
        z = _to_signed16(b1) / scale
        vertices.append((x, y, z))
    if not vertices or tag2_matches < 0.9 * len(vertices):
        return None
    return vertices


def strip_decode(idxs, n_verts, offset, vertices=None, scale=None):
    """Stratégie A : strip standard, fenêtre glissante de 3."""
    remapped = [(v - offset) % n_verts for v in idxs]

    def decode(seq):
        triangles = []
        for i in range(len(seq) - 2):
            A, B, C = seq[i], seq[i + 1], seq[i + 2]
            if A == B or B == C or A == C:
                continue
            triangles.append((A, B, C) if i % 2 == 0 else (B, A, C))
        return triangles

    base_triangles = decode(remapped)

    if len(remapped) >= 2 and vertices and base_triangles:
        wrapped = remapped + [remapped[1]]
        wrapped_triangles = decode(wrapped)
        new_tris = [t for t in wrapped_triangles if t not in base_triangles]
        if new_tris:
            base_max_edge = 0.0
            for A, B, C in base_triangles:
                pA, pB, pC = vertices[A], vertices[B], vertices[C]
                base_max_edge = max(base_max_edge, dist(pA, pB), dist(pB, pC), dist(pC, pA))
            thresh = base_max_edge * 1.2
            accepted = []
            for A, B, C in new_tris:
                pA, pB, pC = vertices[A], vertices[B], vertices[C]
                if max(dist(pA, pB), dist(pB, pC), dist(pC, pA)) <= thresh:
                    accepted.append((A, B, C))
            base_triangles = base_triangles + accepted

    return base_triangles


def group6_quads(idxs, n_verts, offset):
    """Stratégie B : groupes de 6 [A,B,B,C,C,D]."""
    quads = []
    for q in range(len(idxs) // 6):
        g = idxs[q * 6 : q * 6 + 6]
        if len(g) < 6:
            break
        if not (g[1] == g[2] and g[3] == g[4]):
            return None
        A = (g[0] - offset) % n_verts
        B = (g[1] - offset) % n_verts
        C = (g[3] - offset) % n_verts
        D = (g[5] - offset) % n_verts
        quads.append((A, B, C, D))
    return quads


def group6_to_triangles(quads):
    """Convertit des quads en triangles."""
    triangles = []
    for A, B, C, D in quads:
        if len({A, B, C}) == 3:
            triangles.append((A, B, C))
        if len({B, D, C}) == 3:
            triangles.append((B, D, C))
    return triangles


def local_scale(vertices, eps=1e-4):
    """Calcule l'échelle locale basée sur les distances entre voisins."""
    n = len(vertices)
    if n < 2:
        return 1.0
    
    sample_size = min(n, 1000)
    nn_dists = []
    for i in range(sample_size):
        p1 = vertices[i]
        min_dist = float('inf')
        for j in range(min(n, i + 100)):
            if i != j:
                d = dist(p1, vertices[j])
                if d > eps and d < min_dist:
                    min_dist = d
        if min_dist != float('inf'):
            nn_dists.append(min_dist)
    
    if not nn_dists:
        return 1.0
    nn_dists.sort()
    return nn_dists[len(nn_dists) // 2]


def coherence_score(triangles, vertices, scale=None, outlier_factor=6.0):
    """Score de cohérence pour évaluer une triangulation."""
    if not triangles:
        return 0, 0
    if scale is None:
        scale = local_scale(vertices)
    thresh = outlier_factor * scale
    good = 0
    for A, B, C in triangles:
        pA, pB, pC = vertices[A], vertices[B], vertices[C]
        if max(dist(pA, pB), dist(pB, pC), dist(pC, pA)) <= thresh:
            good += 1
    return good, len(triangles)


def orient_outward(triangles, vertices):
    """Corrige le winding des triangles."""
    if not vertices:
        return triangles
    xs = [v[0] for v in vertices]; ys = [v[1] for v in vertices]; zs = [v[2] for v in vertices]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)

    def cross(a, b):
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    def sub(a, b):
        return tuple(a[i]-b[i] for i in range(3))
    def dot(a, b):
        return sum(a[i]*b[i] for i in range(3))
    def norm(v):
        l = math.sqrt(dot(v, v))
        return (0, 0, 0) if l == 0 else tuple(k/l for k in v)

    oriented = []
    for A, B, C in triangles:
        pA, pB, pC = vertices[A], vertices[B], vertices[C]
        n = norm(cross(sub(pB, pA), sub(pC, pA)))
        centroid = tuple((pA[i]+pB[i]+pC[i])/3 for i in range(3))
        outward_ref = norm(sub(centroid, center))
        if dot(n, outward_ref) < 0:
            oriented.append((A, C, B))
        else:
            oriented.append((A, B, C))
    return oriented


def decode_ddm(filepath, out_obj, verbose=True):
    """Décode un fichier DDM en OBJ."""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    if data[:4] != MAGIC:
        raise ValueError(f"{filepath}: pas un fichier DDM (magic={data[:4]!r})")

    # Trouver tous les headers
    headers = find_geometry_headers(data)
    if not headers:
        raise ValueError(f"{filepath}: aucun header de géométrie valide trouvé")

    # Trouver le buffer de vertices partagé
    # Méthode 1: Chercher le marqueur 0xFFFFFFFF après le dernier header
    last_header_pos = max(h[0] for h in headers)
    last_idx_end = last_header_pos + 0x14 + max(h[3] for h in headers if h[0] == last_header_pos) * 2
    
    vbuf = find_vertex_buffer(data, last_idx_end, len(data) - 16)
    vif_mode = False
    
    if vbuf is None:
        # Méthode 2: Chercher avant le premier header
        first_header_pos = min(h[0] for h in headers)
        vbuf = find_vertex_buffer(data, 0, first_header_pos)
        
        if vbuf is None:
            # Méthode 3: Chercher entre les headers
            for i in range(len(headers) - 1):
                h1_pos = headers[i][0]
                h2_pos = headers[i+1][0]
                vbuf = find_vertex_buffer(data, h1_pos + 0x14, h2_pos)
                if vbuf is not None:
                    break
        
        if vbuf is None:
            # Méthode 4: Essayer le format VIF
            # Utiliser le nb_verts du premier header
            first_header = headers[0]
            if len(first_header) >= 2:
                nb_verts_first = first_header[1]
                idx_end_first = first_header[0] + 0x14 + first_header[3] * 2
                vif_vertices = try_vif_vertex_buffer(data, idx_end_first, nb_verts_first)
                if vif_vertices is not None:
                    vertices = vif_vertices
                    vif_mode = True
                    vbuf_start = idx_end_first
                    vbuf_size = nb_verts_first
                else:
                    raise ValueError(f"{filepath}: impossible de trouver le buffer de vertices")
            else:
                raise ValueError(f"{filepath}: impossible de trouver le buffer de vertices")
        else:
            vbuf_start, vbuf_size = vbuf
            # Lire tous les vertices
            vertices = load_vertices(data, vbuf_start, vbuf_size, '>')
    else:
        vbuf_start, vbuf_size = vbuf
        # Lire tous les vertices
        vertices = load_vertices(data, vbuf_start, vbuf_size, '>')

    if len(vertices) < 3:
        raise ValueError(f"{filepath}: pas assez de vertices ({len(vertices)})")

    if verbose:
        print(f"Buffer de vertices: {vbuf_start:#x} ({vbuf_size} vertices)")
        print(f"Vertices charés: {len(vertices)}")

    # Traiter chaque section géométrique
    all_triangles = []
    sections_processed = 0
    sections_failed = 0

    for header_data in headers:
        if len(header_data) == 5:
            header_pos, nb_verts, nb_sub, nb_idx, endian = header_data
        else:
            header_pos, nb_verts, nb_sub, nb_idx = header_data
            endian = '>'

        idx_start = header_pos + 0x14
        
        # Vérifier que les indices ne dépassent pas
        if idx_start + nb_idx * 2 > len(data):
            if verbose:
                print(f"  header@{hex(header_pos)}: ignore (indices hors limite)")
            continue
        
        # Lire les indices
        if endian == '<':
            idxs_full = [
                struct.unpack("<H", data[idx_start + i * 2 : idx_start + i * 2 + 2])[0]
                for i in range(nb_idx)
            ]
        else:
            idxs_full = [
                struct.unpack(">H", data[idx_start + i * 2 : idx_start + i * 2 + 2])[0]
                for i in range(nb_idx)
            ]

        # Retirer les marqueurs de fin de buffer
        idxs = list(idxs_full)
        while idxs and idxs[-1] >= len(vertices):
            idxs.pop()

        if len(idxs) < 3:
            if verbose:
                print(f"  header@{hex(header_pos)}: ignore (trop peu d'indices)")
            continue

        # Essayer les deux stratégies
        try:
            # Stratégie A: strip standard
            strip_tris = strip_decode(idxs, len(vertices), 0, vertices=vertices)
            
            # Stratégie B: groupes de 6
            quads = group6_quads(idxs, len(vertices), 0)
            if quads:
                group_tris = group6_to_triangles(quads)
            else:
                group_tris = []

            # Choisir la meilleure
            if strip_tris and group_tris:
                # Choisir celle avec le plus de triangles
                if len(strip_tris) >= len(group_tris):
                    tris = strip_tris
                    strategy = "strip"
                else:
                    tris = group_tris
                    strategy = "group6"
            elif strip_tris:
                tris = strip_tris
                strategy = "strip"
            elif group_tris:
                tris = group_tris
                strategy = "group6"
            else:
                if verbose:
                    print(f"  header@{hex(header_pos)}: aucune stratégie n'a fonctionné")
                sections_failed += 1
                continue

            all_triangles.extend(tris)
            sections_processed += 1
            if verbose:
                print(f"  header@{hex(header_pos)}: {strategy}, {len(tris)} triangles")

        except Exception as e:
            if verbose:
                print(f"  header@{hex(header_pos)}: ERREUR - {e}")
            sections_failed += 1

    if not all_triangles:
        raise ValueError(f"{filepath}: aucune section n'a pu être décodée")

    # Dedupliquer les vertices finaux et remapper
    # (Créer un mapping unique)
    vertex_to_idx = {}
    unique_vertices = []
    for v in vertices:
        v_tuple = tuple(v)
        if v_tuple not in vertex_to_idx:
            vertex_to_idx[v_tuple] = len(unique_vertices)
            unique_vertices.append(v_tuple)
    
    # Remapper tous les triangles
    remapped_triangles = []
    for A, B, C in all_triangles:
        A_idx = vertex_to_idx.get(tuple(vertices[A]), 0)
        B_idx = vertex_to_idx.get(tuple(vertices[B]), 0)
        C_idx = vertex_to_idx.get(tuple(vertices[C]), 0)
        remapped_triangles.append((A_idx, B_idx, C_idx))

    vertices = unique_vertices
    all_triangles = remapped_triangles

    if verbose:
        print(f"Deduplication: {len(vertices)} vertices uniques")
        print(f"Total: {len(all_triangles)} triangles")

    # Corriger le winding
    all_triangles = orient_outward(all_triangles, vertices)

    # Écrire le fichier OBJ
    with open(out_obj, 'w') as f:
        f.write(f"# {os.path.basename(filepath)} - decode FINAL ({len(vertices)} verts, {len(all_triangles)} tris)\n")
        for v in vertices:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for A, B, C in all_triangles:
            f.write(f"f {A+1} {B+1} {C+1}\n")

    if verbose:
        print(f">>> SAUVE: {out_obj} ({len(vertices)} verts, {len(all_triangles)} triangles)\n")

    return len(vertices), len(all_triangles)


def batch_process(root, out_dir, verbose=False):
    """Traite tous les fichiers DDM d'un dossier."""
    root = Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = [p for p in root.rglob("*") if p.is_file()]
    ddm_files = []
    for p in candidates:
        try:
            with open(p, 'rb') as f:
                if f.read(4) == MAGIC:
                    ddm_files.append(p)
        except Exception:
            continue

    print(f"{len(ddm_files)} fichiers .ddm trouvés dans {root}\n")

    results = []
    for p in ddm_files:
        out_name = out_dir / (p.stem + "_final.obj")
        try:
            if verbose:
                print(f"=== {p.name} ===")
            n_verts, n_tris = decode_ddm(str(p), str(out_name), verbose=verbose)
            results.append((p, "OK", n_verts, n_tris))
            if verbose:
                print()
        except Exception as e:
            results.append((p, f"ERREUR: {e}", 0, 0))
            if verbose:
                print(f"[FAIL] {p.name}: {e}\n")

    ok = [r for r in results if r[1] == "OK"]
    failed = [r for r in results if r[1] != "OK"]

    report_path = out_dir / "_rapport_final.txt"
    with open(report_path, 'w') as f:
        f.write(f"Rapport de decodage DDM FINAL en lot\n")
        f.write(f"Total: {len(results)}  OK: {len(ok)}  Echecs: {len(failed)}\n\n")
        f.write("=== ECHECS ===\n")
        for p, status, _, _ in failed:
            f.write(f"{p}: {status}\n")
        f.write("\n=== OK ===\n")
        for p, status, nv, nt in ok:
            f.write(f"{p}: {nv} verts, {nt} tris\n")

    print(f"\nTerminé : {len(ok)}/{len(results)} décodés avec succès ({len(failed)} échecs)")
    print(f"Rapport complet : {report_path}")
    print(f"OBJ générés dans : {out_dir}")
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Decodeur DDM FINAL -> OBJ")
    ap.add_argument("input", nargs="+", help="Fichier(s) .ddm/.bin, ou un dossier avec --batch")
    ap.add_argument("--batch", action="store_true", help="Traiter l'entrée comme un dossier (recursif)")
    ap.add_argument("--out", default=".", help="Dossier de sortie (mode --batch uniquement)")
    args = ap.parse_args()

    if args.batch:
        batch_process(args.input[0], args.out, verbose=True)
    else:
        for fname in args.input:
            try:
                print(f"=== {fname} ===")
                out = os.path.splitext(fname)[0] + "_final.obj"
                decode_ddm(fname, out)
            except Exception as e:
                print(f"ERREUR : {e}\n")
