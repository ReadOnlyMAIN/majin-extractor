"""
Legacy multi-section DDM -> OBJ prototype
===========================

Retained only as a research reference for multi-section DDM variants,
especially chr520. The maintained converter is located at
tools/conversion/ddm_to_obj.py.

Historical features:
- Filtering of metadata sections (section_0 is often invalid)
- Correct handling of triangle-strip restarts
- Filtering of inconsistent triangles (oversized, invalid indices)
- Automatic distinction between valid geometry and metadata sections
- Improved restart detection ([A,A] or [A,B,B] patterns)

Resolved issues:
- Extra faces caused by incorrect restart handling
- Section 0 containing metadata instead of geometry
- Degenerate or inconsistent triangles

Usage:
  python ddm_decoder_clean_v2.py file.ddm --out output_directory
  python ddm_decoder_clean_v2.py directory/ --batch --out output_directory
"""

import struct
import math
import sys
import os
from pathlib import Path

MAGIC = b"\x00ddm"


def dist(p1, p2):
    """Compute the Euclidean distance between two points."""
    return math.sqrt(sum((p1[k] - p2[k]) ** 2 for k in range(3)))


def is_metadata_section(nb_verts, nb_sub, nb_idx, data, idx_start):
    """
    Determine whether a section probably contains metadata rather than geometry.
    
    Metadata criteria:
    - Very small nb_verts (< 10) with a very large nb_idx
    - nb_idx < 3 (too few indices to form triangles)
    - Excessively high nb_idx/nb_verts ratio (> 100)
    - Indices that do not form a coherent strip pattern
    """
    # Obvious cases
    if nb_verts < 3 or nb_idx < 3:
        return True
    
    # Excessively high ratio
    if nb_verts > 0 and nb_idx / nb_verts > 100:
        return True
    
    # Very small vertex count with many indices
    if nb_verts < 10 and nb_idx > 100:
        return True
    
    # Inspect the first indices for suspicious patterns.
    if idx_start + 6 <= len(data):
        first_indices = []
        for i in range(min(6, nb_idx)):
            idx = struct.unpack(">H", data[idx_start + i*2:idx_start + i*2 + 2])[0]
            first_indices.append(idx)
        
        # Reject identical or heavily repeated initial indices.
        unique_indices = set(first_indices)
        if len(unique_indices) == 1:
            return True
    
    return False


def find_geometry_headers(data):
    """Find geometry-section headers (\x00\x00\x00\x07 + counts)."""
    pat = bytes([0, 0, 0, 7])
    pos = 0
    headers = []
    while True:
        pos = data.find(pat, pos)
        if pos == -1:
            break
        if pos + 16 <= len(data):
            # Try both byte orders.
            nb_verts_be, nb_sub_be, nb_idx_be = struct.unpack(">III", data[pos + 4 : pos + 16])
            nb_verts_le, nb_sub_le, nb_idx_le = struct.unpack("<III", data[pos + 4 : pos + 16])
            
            def is_plausible(nv, ns, ni):
                # Reject clearly implausible values.
                if nv < 3 or ni < 3:
                    return False
                # Reject an excessively large vertex count.
                if nv > 100000:
                    return False
                # Reject an excessively large index count.
                if ni > 1000000:
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
    """Find the vertex buffer (0xFFFFFFFF marker at a 24-byte stride)."""
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
    """Load vertex positions (12 position bytes per 24-byte record)."""
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
    """Load a compressed VIF/GS vertex buffer."""
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


def decode_strip_with_restarts(idxs, n_verts, offset=0):
    """
    Decode a triangle strip while detecting restarts.
    
    In triangle strips:
    - Each triplet (i, i+1, i+2) forms a triangle
    - Winding alternates: (A,B,C) for even i, (B,A,C) for odd i
    - Repeated indices indicate restarts: [A,A] or [A,B,B]
    
    This function:
    1. Removes end-of-buffer markers (indices >= n_verts)
    2. Detects restarts (pairs of identical indices)
    3. Splits the strip into smaller bands
    4. Decodes each band independently
    5. Filters degenerate triangles
    """
    # Normalize out-of-range indices.
    idxs = [(v - offset) % n_verts for v in idxs]
    
    # Detect restart positions.
    restart_positions = []
    for i in range(len(idxs) - 1):
        if idxs[i] == idxs[i + 1]:
            restart_positions.append(i + 1)  # The restart follows the pair.
    
    # Decode the complete strip when it has no restart.
    if not restart_positions:
        return decode_strip(idxs)
    
    # Split into bands.
    bands = []
    start = 0
    for rp in restart_positions:
        if rp > start:
            bands.append(idxs[start:rp])
        start = rp
    if start < len(idxs):
        bands.append(idxs[start:])
    
    # Decode each band.
    all_triangles = []
    for band in bands:
        if len(band) >= 3:
            all_triangles.extend(decode_strip(band))
    
    return all_triangles


def decode_strip(idxs):
    """
    Decode a triangle strip without restarts.
    
    In a standard strip, each triplet (i, i+1, i+2) forms a triangle with
    alternating winding.
    """
    triangles = []
    for i in range(len(idxs) - 2):
        A, B, C = idxs[i], idxs[i + 1], idxs[i + 2]
        
        # Ignore degenerate triangles with identical indices.
        if A == B or B == C or A == C:
            continue
        
        # Alternating winding.
        if i % 2 == 0:
            triangles.append((A, B, C))
        else:
            triangles.append((B, A, C))
    
    return triangles


def filter_invalid_triangles(triangles, vertices, scale_factor=10.0):
    """
    Filter inconsistent triangles.
    
    Filtering criteria:
    - Edges that are too long relative to the average model scale
    - Degenerate triangles with nearly zero area
    """
    if not vertices or not triangles:
        return triangles
    
    # Compute the local scale from neighboring vertex distances.
    model_scale = local_scale(vertices)
    max_edge_threshold = model_scale * scale_factor
    
    filtered = []
    for A, B, C in triangles:
        pA, pB, pC = vertices[A], vertices[B], vertices[C]
        
        # Compute edge lengths.
        edge_AB = dist(pA, pB)
        edge_BC = dist(pB, pC)
        edge_CA = dist(pC, pA)
        max_edge = max(edge_AB, edge_BC, edge_CA)
        
        # Reject triangles with an excessively long edge.
        if max_edge > max_edge_threshold:
            continue
        
        # Filter nearly zero-area degenerate triangles using a cross product.
        def sub(a, b):
            return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
        
        AB = sub(pB, pA)
        AC = sub(pC, pA)
        # Cross product.
        cross_x = AB[1] * AC[2] - AB[2] * AC[1]
        cross_y = AB[2] * AC[0] - AB[0] * AC[2]
        cross_z = AB[0] * AC[1] - AB[1] * AC[0]
        area = math.sqrt(cross_x**2 + cross_y**2 + cross_z**2)
        
        # A very small area probably indicates a degenerate triangle.
        if area < 1e-6:
            continue
        
        filtered.append((A, B, C))
    
    return filtered


def local_scale(vertices, eps=1e-4):
    """Compute local scale from neighboring vertex distances."""
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
    """Return a coherence score for a triangulation."""
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
    """Orient triangle winding outward."""
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


def decode_ddm_clean(filepath, out_obj, verbose=True):
    """Decode a DDM file to OBJ with the experimental decoder."""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    if data[:4] != MAGIC:
        raise ValueError(f"{filepath}: not a DDM file (magic={data[:4]!r})")

    # Find all geometry headers.
    headers = find_geometry_headers(data)
    if not headers:
        raise ValueError(f"{filepath}: no valid geometry header found")

    if verbose:
        print(f"Found {len(headers)} geometry headers")

    # Find the shared vertex buffer.
    last_header_pos = max(h[0] for h in headers)
    last_idx_end = last_header_pos + 0x14 + max(h[3] for h in headers if h[0] == last_header_pos) * 2
    
    vbuf = find_vertex_buffer(data, last_idx_end, len(data) - 16)
    vif_mode = False
    
    if vbuf is None:
        # Method 2: search before the first header.
        first_header_pos = min(h[0] for h in headers)
        vbuf = find_vertex_buffer(data, 0, first_header_pos)
        
        if vbuf is None:
            # Method 3: search between headers.
            for i in range(len(headers) - 1):
                h1_pos = headers[i][0]
                h2_pos = headers[i+1][0]
                vbuf = find_vertex_buffer(data, h1_pos + 0x14, h2_pos)
                if vbuf is not None:
                    break
        
        if vbuf is None:
            # Method 4: try the VIF format.
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
                    raise ValueError(f"{filepath}: could not locate the vertex buffer")
            else:
                raise ValueError(f"{filepath}: could not locate the vertex buffer")
        else:
            vbuf_start, vbuf_size = vbuf
            vertices = load_vertices(data, vbuf_start, vbuf_size, '>')
    else:
        vbuf_start, vbuf_size = vbuf
        vertices = load_vertices(data, vbuf_start, vbuf_size, '>')

    if len(vertices) < 3:
        raise ValueError(f"{filepath}: not enough vertices ({len(vertices)})")

    if verbose:
        print(f"Vertex buffer: {vbuf_start:#x} ({vbuf_size} vertices)")
        print(f"Loaded vertices: {len(vertices)}")

    # Traiter chaque section géométrique
    all_triangles = []
    sections_processed = 0
    sections_failed = 0
    sections_skipped = 0

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
            sections_skipped += 1
            continue
        
        # Vérifier si c'est une section de métadonnées
        if is_metadata_section(nb_verts, nb_sub, nb_idx, data, idx_start):
            if verbose:
                print(f"  header@{hex(header_pos)}: ignore (métadonnées, {nb_verts}v/{nb_idx}i)")
            sections_skipped += 1
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
            sections_skipped += 1
            continue

        # Essayer le décodage avec gestion des restarts
        try:
            tris = decode_strip_with_restarts(idxs, len(vertices), 0)
            
            if not tris:
                if verbose:
                    print(f"  header@{hex(header_pos)}: aucun triangle décodé")
                sections_failed += 1
                continue

            # Filtrer les triangles invalides
            tris = filter_invalid_triangles(tris, vertices, scale_factor=10.0)
            
            if not tris:
                if verbose:
                    print(f"  header@{hex(header_pos)}: tous les triangles filtrés")
                sections_failed += 1
                continue

            all_triangles.extend(tris)
            sections_processed += 1
            if verbose:
                print(f"  header@{hex(header_pos)}: {len(tris)} triangles (après filtrage)")

        except Exception as e:
            if verbose:
                print(f"  header@{hex(header_pos)}: ERREUR - {e}")
            sections_failed += 1

    if not all_triangles:
        raise ValueError(f"{filepath}: aucune section n'a pu être décodée")

    if verbose:
        print(f"\nSections: {sections_processed} traitées, {sections_failed} échouées, {sections_skipped} ignorées")
        print(f"Triangles totaux: {len(all_triangles)}")

    # Dedupliquer les vertices finaux et remapper
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

    # Corriger le winding
    all_triangles = orient_outward(all_triangles, vertices)

    # Écrire le fichier OBJ
    with open(out_obj, 'w') as f:
        f.write(f"# {os.path.basename(filepath)} - CLEAN V2 ({len(vertices)} verts, {len(all_triangles)} tris)\n")
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
        out_name = out_dir / (p.stem + "_clean_v2.obj")
        try:
            if verbose:
                print(f"=== {p.name} ===")
            n_verts, n_tris = decode_ddm_clean(str(p), str(out_name), verbose=verbose)
            results.append((p, "OK", n_verts, n_tris))
            if verbose:
                print()
        except Exception as e:
            results.append((p, f"ERREUR: {e}", 0, 0))
            if verbose:
                print(f"[FAIL] {p.name}: {e}\n")

    ok = [r for r in results if r[1] == "OK"]
    failed = [r for r in results if r[1] != "OK"]

    report_path = out_dir / "_rapport_clean_v2.txt"
    with open(report_path, 'w') as f:
        f.write(f"Rapport de decodage DDM CLEAN V2 en lot\n")
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
    ap = argparse.ArgumentParser(description="Decodeur DDM CLEAN V2 -> OBJ")
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
                out = os.path.splitext(fname)[0] + "_clean_v2.obj"
                decode_ddm_clean(fname, out)
            except Exception as e:
                print(f"ERREUR : {e}\n")
