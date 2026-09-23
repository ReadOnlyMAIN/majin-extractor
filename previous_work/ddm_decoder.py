"""
Decodeur DDM generique -> OBJ
==============================

Fonctionne sur n'importe quel fichier .ddm (magic \\x00ddm) en detectant
automatiquement :
  1. Le(s) header(s) de section geometrie (pattern \\x00\\x00\\x00\\x07)
  2. Le buffer de vertices (recherche du marqueur 0xFFFFFFFF a stride 24,
     qui borne la position + le flag de fin de vertex sur les fichiers
     testes jusqu'ici)
  3. La bonne strategie de decodage des indices, entre :
       - STRIP STANDARD : lecture glissante (i, i+1, i+2), triangles
         degeneres (2 indices egaux) ignores, winding alterne. Marche pour
         les objets "simples" (gim527, gim136).
       - GROUPES DE 6 [A,B,B,C,C,D] : necessite un offset de remapping
         (index_brut - offset) % nb_vertices_reels. Marche pour les objets
         plus complexes ou nb_verts (header) > nb positions reellement
         stockees (gim235, ou l'index le plus haut depasse la table de
         positions dedupliquee).
  4. Le meilleur offset (0 si pas necessaire) par scan systematique,
     evalue par un score de coherence geometrique (aretes courtes).

La strategie ET l'offset qui donnent le meilleur score sont choisis
automatiquement, sans reglage manuel par fichier.

Valide sur :
  - gim235.bin -> groupes de 6, offset=82  (118 triangles)
  - gim527.bin -> strip standard, offset=0 (35 triangles)
  - gim136.bin -> strip standard, offset=0 (55 triangles)
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
    """Cherche tous les headers de section geo (\\x00\\x00\\x00\\x07 + nb_verts/nb_sub/nb_idx)."""
    pat = bytes([0, 0, 0, 7])
    pos = 0
    headers = []
    while True:
        pos = data.find(pat, pos)
        if pos == -1:
            break
        if pos + 16 <= len(data):
            nb_verts, nb_sub, nb_idx = struct.unpack(">III", data[pos + 4 : pos + 16])
            # filtre de sanite : valeurs plausibles pour un mesh, ET ratio
            # nb_idx/nb_verts realiste (un vrai strip a rarement plus de
            # ~50x plus d'indices que de sommets -- au-dela c'est un faux
            # positif de pattern qui provoque des calculs enormes pour rien)
            ratio_ok = nb_idx <= max(50 * nb_verts, 200)
            if 3 <= nb_verts <= 200000 and 1 <= nb_sub <= 128 and 3 <= nb_idx <= 2000000 and ratio_ok:
                idx_start = pos + 0x14
                if idx_start + nb_idx * 2 <= len(data):
                    headers.append((pos, nb_verts, nb_sub, nb_idx))
        pos += 1
    return headers


def find_vertex_buffer(data, search_start, search_end, min_run=4):
    """Cherche le meilleur run de marqueurs 0xFFFFFFFF a stride 24 (position du
    buffer de vertices). Utilise bytes.find (implemente en C, tres rapide)
    pour localiser les occurrences du marqueur, plutot que de tester CHAQUE
    position d'un octet a l'autre en Python -- l'ancienne version pouvait
    prendre des dizaines de secondes par header sur un gros fichier (map,
    personnage), multiplie par le nombre de headers candidats."""
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


def load_vertices(data, vbuf_start, n):
    vertices = []
    for i in range(n):
        base = vbuf_start + i * 24
        if base + 12 > len(data):
            break
        x, y, z = struct.unpack(">fff", data[base : base + 12])
        vertices.append((x, y, z))
    return vertices


VIF_TAG2 = b"\x00\x00\x3c\x00"  # constante observee sur tous les enregistrements VIF connus


def _to_signed16(v):
    return v - 65536 if v >= 32768 else v


def try_vif_vertex_buffer(data, vbuf_start, n, scale=16384.0):
    """Charge un buffer de vertices au format VIF/GS compresse decouvert sur
    gim421/gim502/gim301 : enregistrements de 16 octets, [tag1 4o][X 2o][Y 2o]
    [tag2 4o = constante 0x00003C00][Z 2o][pad 2o]. Coordonnees en int16
    signe divise par 16384 (virgule fixe ~Q2.14, format Vector Unit PS2).
    Retourne None si la constante attendue n'apparait pas assez souvent
    (signe que ce n'est pas ce format)."""
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
        return None  # pas assez de correspondances -> probablement pas ce format
    return vertices


def strip_decode_flagged(idxs, n_verts, offset):
    """Comme strip_decode, mais marque aussi chaque triangle qui vient d'etre
    emis juste APRES un rebond de degenerescence (repetition d'indices pour
    changer de direction dans le strip). Ce sont ces triangles-la, et
    seulement ceux-la, qui peuvent accidentellement 'sauter' un sommet-
    charniere et relier deux parties non adjacentes de l'objet (observe sur
    gim127 et gim136). Le reste du strip (loin de tout rebond) est laisse
    intact, meme si ses aretes sont grandes -- une grande planche plate est
    une geometrie legitime, pas un artefact.

    Inclut aussi le bouclage de fin de strip (cf strip_decode) ; le triangle
    de fermeture est lui aussi marque comme suspect, pour beneficier du meme
    controle de coherence."""
    remapped = [(v - offset) % n_verts for v in idxs]

    def decode(seq):
        triangles = []
        flags = []
        bounce_countdown = 0
        for i in range(len(seq) - 2):
            A, B, C = seq[i], seq[i + 1], seq[i + 2]
            if A == B or B == C or A == C:
                bounce_countdown = 2  # le sommet saute affecte les 2 triangles suivants
                continue
            triangles.append((A, B, C) if i % 2 == 0 else (B, A, C))
            flags.append(1 if bounce_countdown > 0 else 0)
            if bounce_countdown > 0:
                bounce_countdown -= 1
        return triangles, flags

    triangles, flags = decode(remapped)
    flags = [1 if f else 0 for f in flags]  # 0=normal, 1=rebond milieu de strip

    # Bouclage de fin de strip : plutot que de re-decoder TOUTE la sequence
    # avec 1 element ajoute puis comparer chaque triangle a la liste entiere
    # (O(n^2), catastrophique sur les gros fichiers -- 12s+ sur un fichier de
    # map a 30000+ sommets), on calcule directement la ou les nouvelles
    # fenetres crees par l'ajout, qui sont forcement en toute fin de sequence.
    if len(remapped) >= 2:
        wrapped_seq = remapped + [remapped[1]]
        n = len(wrapped_seq)
        # les nouvelles fenetres sont celles qui utilisent l'element ajoute,
        # soit les 2 dernieres positions possibles de la sequence bouclee
        for i in range(max(0, n - 3), n - 2):
            A, B, C = wrapped_seq[i], wrapped_seq[i + 1], wrapped_seq[i + 2]
            if A == B or B == C or A == C:
                continue
            t = (A, B, C) if i % 2 == 0 else (B, A, C)
            triangles.append(t)
            flags.append(2)  # 2 = triangle de fermeture de boucle (controle strict)

    return triangles, flags


def strip_decode(idxs, n_verts, offset, vertices=None, scale=None):
    """Strategie A : strip standard, fenetre glissante de 3, degeneres ignores.
    Le strip peut former une boucle fermee (le dernier sommet ne revient au
    debut qu'une seule fois dans le flux brut) -- sans bouclage explicite, le
    triangle de fermeture est invisible car aucune fenetre de la liste
    lineaire ne peut le voir. On ajoute donc 1-2 elements du debut a la fin,
    MAIS on ne garde les triangles supplementaires que s'ils passent le meme
    test de coherence (aretes proches de l'echelle naturelle du nuage de
    points) que le reste -- sinon on obtient des diagonales parasites
    traversant tout l'objet (observe sur gim136 : pole nord - pole sud)."""
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
            # seuil strict : le candidat ne doit pas depasser la plus grande
            # arete deja validee parmi les triangles de base (+marge de 20%).
            # local_scale seul est trop permissif ici (un objet compact peut
            # avoir un diametre bien plus grand que l'espacement typique entre
            # sommets voisins, ce qui laissait passer une diagonale pole-a-pole
            # parasite sur gim136).
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


def group6_quads(idxs, n_verts, offset, vertices=None, scale=None):
    """Strategie B : groupes de 6 [A,B,B,C,C,D] -> renvoie les quads bruts
    (A,B,C,D) remappes. La conversion en triangles et le filtrage se font
    separement (cf group6_to_triangles / quad_diagonal_score)."""
    quads = []
    for q in range(len(idxs) // 6):
        g = idxs[q * 6 : q * 6 + 6]
        if len(g) < 6:
            break
        if not (g[1] == g[2] and g[3] == g[4]):
            return None  # ce fichier ne suit pas ce pattern -> strategie invalide
        A = (g[0] - offset) % n_verts
        B = (g[1] - offset) % n_verts
        C = (g[3] - offset) % n_verts
        D = (g[5] - offset) % n_verts
        quads.append((A, B, C, D))
    return quads


def group6_to_triangles(quads):
    """Convertit une liste de quads (A,B,C,D) filtres en triangles finaux."""
    triangles = []
    for A, B, C, D in quads:
        if len({A, B, C}) == 3:
            triangles.append((A, B, C))
        if len({B, D, C}) == 3:
            triangles.append((B, D, C))
    return triangles


def bbox_diagonal(vertices):
    xs = [v[0] for v in vertices]; ys = [v[1] for v in vertices]; zs = [v[2] for v in vertices]
    return dist((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return 0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _grid_nn_distances(vertices, eps=1e-4):
    """Distance au plus proche voisin DISTINCT pour chaque sommet, calculee
    via une grille spatiale (~O(n)) au lieu d'une comparaison brute de
    chaque paire (O(n^2), catastrophique au-dela de quelques milliers de
    sommets -- un fichier a 50 000+ sommets pouvait bloquer le script
    plusieurs minutes, voire plus, avec l'ancienne methode)."""
    n = len(vertices)
    if n < 2:
        return [1.0] * n

    xs = [v[0] for v in vertices]; ys = [v[1] for v in vertices]; zs = [v[2] for v in vertices]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), eps)
    # taille de cellule visant ~1-2 points par cellule en moyenne
    cell_size = max(span / max(1, round(n ** (1 / 3.0))), eps)

    def cell_of(p):
        return (int(p[0] // cell_size), int(p[1] // cell_size), int(p[2] // cell_size))

    grid = {}
    cells = []
    for i, p in enumerate(vertices):
        c = cell_of(p)
        cells.append(c)
        grid.setdefault(c, []).append(i)

    nn = [None] * n
    for i, p in enumerate(vertices):
        cx, cy, cz = cells[i]
        best = None
        radius = 1
        while best is None and radius <= 8:
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    for dz in range(-radius, radius + 1):
                        # ne re-scanner que la coquille exterieure au-dela de radius=1
                        if radius > 1 and max(abs(dx), abs(dy), abs(dz)) < radius:
                            continue
                        for j in grid.get((cx + dx, cy + dy, cz + dz), ()):
                            if j == i:
                                continue
                            d = dist(p, vertices[j])
                            if d <= eps:
                                continue
                            if best is None or d < best:
                                best = d
            radius += 1
        nn[i] = best if best is not None else 1.0
    return nn


def per_vertex_nn_distance(vertices, eps=1e-4):
    """Distance au plus proche voisin DISTINCT, calculee individuellement pour
    CHAQUE sommet (pas une seule valeur globale pour tout l'objet). Un objet
    complexe (ex: bateau avec mats fins + coque large) a une densite tres
    variable selon la zone -- une echelle globale unique echoue a distinguer
    correctement les deux. Retourne une liste parallele a `vertices`."""
    return _grid_nn_distances(vertices, eps)


def local_scale(vertices, eps=1e-4):
    """Echelle de reference INDEPENDANTE de la triangulation candidate :
    distance mediane au plus proche voisin DISTINCT dans le nuage de points
    brut (les doublons exacts de position, frequents aux coutures UV, sont
    ignores sinon ils ecrasent l'echelle a ~0). Sert a juger si une
    triangulation produit des aretes plausibles ou non, sans etre trompe par
    un decodage systematiquement mauvais (ou tout est egalement grand, donc
    rien ne ressort comme "outlier" en interne)."""
    n = len(vertices)
    if n < 2:
        return 1.0
    nn = _grid_nn_distances(vertices, eps)
    return max(median(nn), 1e-6) if nn else 1.0


def coherence_score(triangles, vertices, scale=None, outlier_factor=6.0):
    """Pour la strategie STRIP : un vrai strip relie des sommets physiquement
    adjacents, donc TOUTES les aretes d'un triangle bien decode devraient
    rester proches de l'echelle naturelle du nuage de points."""
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


def quad_diagonal_score(quads, vertices, scale=None, outlier_factor=6.0, planar_factor=1.0):
    """Pour la strategie GROUPES DE 6 [A,B,B,C,C,D] : chaque triangle du quad
    est teste INDEPENDAMMENT (comme valide manuellement) :
      - triangle (A,B,C) garde si dist(A,C) courte OU quad plat
      - triangle (B,D,C) garde si dist(B,D) courte OU quad plat
    (un quad plat, comme le couvercle, est toujours garde en entier meme si
    ses aretes/diagonales sont grandes). Le score retourne le nombre de
    TRIANGLES (pas de quads) qui passent, sur le total possible (2 par quad)."""
    if not quads:
        return 0, 0
    if scale is None:
        scale = local_scale(vertices)
    diag_thresh = outlier_factor * scale
    planar_thresh = planar_factor * scale
    good = 0
    total = 0
    for A, B, C, D in quads:
        t1, t2 = _quad_triangle_accept(A, B, C, D, vertices, diag_thresh, planar_thresh)
        total += 2
        good += int(t1) + int(t2)
    return good, total


def _quad_triangle_accept(A, B, C, D, vertices, diag_thresh, planar_thresh):
    """Retourne (garder_ABC, garder_BDC)."""
    pA, pB, pC, pD = vertices[A], vertices[B], vertices[C], vertices[D]
    planar = _quad_is_planar(pA, pB, pC, pD, planar_thresh)
    if planar:
        return True, True
    t1 = dist(pA, pC) <= diag_thresh
    t2 = dist(pB, pD) <= diag_thresh
    return t1, t2


def _quad_is_planar(pA, pB, pC, pD, planar_thresh):
    def sub(a, b): return tuple(a[i]-b[i] for i in range(3))
    def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    def dot(a, b): return sum(a[i]*b[i] for i in range(3))
    n = cross(sub(pB, pA), sub(pC, pA))
    nlen = math.sqrt(dot(n, n))
    if nlen < 1e-9:
        return False
    n = tuple(k/nlen for k in n)
    dev = abs(dot(sub(pD, pA), n))
    return dev <= planar_thresh


def best_offset_scan(idxs, vertices, n_verts, strategy_fn, scorer_fn, offsets_to_try=None):
    if offsets_to_try is None:
        offsets_to_try = range(n_verts)
    scale = local_scale(vertices)
    best = None
    for off in offsets_to_try:
        result = strategy_fn(idxs, n_verts, off, vertices=vertices, scale=scale)
        if result is None:
            continue
        g, t = scorer_fn(result, vertices, scale=scale)
        ratio = g / t if t else 0
        if best is None or (ratio, g) > (best[0], best[1]):
            best = (ratio, g, t, off, result)
    return best


def orient_outward(triangles, vertices):
    """Corrige le winding de chaque triangle pour que sa normale pointe vers
    l'exterieur (direction centre-bbox -> centroide du triangle). Utile pour
    les maillages non-manifold (treillis) ou la propagation d'orientation
    classique (edge-based) ne fonctionne pas."""
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


def decode_ddm(path, out_obj, verbose=True):
    data = open(path, "rb").read()
    if data[:4] != MAGIC:
        raise ValueError(f"{path}: pas un fichier DDM (magic={data[:4]!r})")

    headers = find_geometry_headers(data)
    if not headers:
        raise ValueError(f"{path}: aucun header de geometrie valide trouve")

    # essaie chaque header trouve (certains hits peuvent etre des faux positifs
    # dans les strings du materiau), garde celui qui produit le meilleur resultat
    best_overall = None
    for header_pos, nb_verts, nb_sub, nb_idx in headers:
        idx_start = header_pos + 0x14
        idxs_full = [
            struct.unpack(">H", data[idx_start + i * 2 : idx_start + i * 2 + 2])[0]
            for i in range(nb_idx)
        ]
        idx_end = idx_start + nb_idx * 2

        vbuf = find_vertex_buffer(data, idx_end, len(data) - 16)
        vif_mode = False
        if vbuf is None:
            # repli : format VIF/GS compresse (positions en int16 fixe, pas
            # de marqueur 0xFFFFFFFF) -- le buffer suit directement les
            # indices, avec nb_verts venant du header
            vif_vertices = try_vif_vertex_buffer(data, idx_end, nb_verts)
            if vif_vertices is None:
                continue
            vertices = vif_vertices
            vif_mode = True
            vbuf_start = idx_end
        else:
            vbuf_start, run = vbuf
            n_verts_use = run
            vertices = load_vertices(data, vbuf_start, n_verts_use)
        if len(vertices) < 3:
            continue

        # Controle de ratio sur le nombre de sommets REELLEMENT charges (pas
        # celui declare dans le header, qui peut etre trompeur si la
        # recherche du buffer n'a trouve qu'un petit run coincidental) : si
        # nb_idx est demesure par rapport aux vertices trouves, tout index
        # sera enroule (modulo) sur une poignee de sommets et produira des
        # dizaines de milliers de triangles degeneres pour rien -- on saute.
        if nb_idx > max(50 * len(vertices), 200):
            if verbose:
                print(f"  header@{hex(header_pos)} ignore : ratio nb_idx/vertices reels ({nb_idx}/{len(vertices)}) implausible")
            continue

        # retirer les marqueurs de fin de buffer (index >= nb vertices reels, en fin de liste)
        idxs = list(idxs_full)
        while idxs and idxs[-1] >= len(vertices):
            idxs.pop()

        # Strategie A : strip standard, offset=0 uniquement (les strips ne
        # necessitent generalement pas d'offset -- teste large par securite)
        res_strip = best_offset_scan(
            idxs, vertices, len(vertices), strip_decode, coherence_score, offsets_to_try=[0]
        )

        # Strategie B : groupes de 6, scan des offsets (le pattern par groupes
        # de 6 n'a ete observe que sur des objets a quelques centaines de
        # sommets -- au-dela, scanner tous les offsets un par un serait
        # beaucoup trop lent pour un interet quasi nul, donc on limite)
        n_verts_g6 = len(vertices)
        if n_verts_g6 <= 2000:
            g6_offsets = range(n_verts_g6)
        else:
            g6_offsets = range(0, n_verts_g6, max(1, n_verts_g6 // 500))
        res_group6 = best_offset_scan(
            idxs, vertices, len(vertices), group6_quads, quad_diagonal_score,
            offsets_to_try=g6_offsets,
        )

        candidates = []
        if res_strip is not None:
            candidates.append(("strip standard", res_strip))
        if res_group6 is not None:
            candidates.append(("groupes de 6", res_group6))
        if not candidates:
            continue

        strategy_name, (ratio, g, t, off, result) = max(candidates, key=lambda c: (c[1][0], c[1][1]))

        if verbose:
            vif_tag = " [VIF]" if vif_mode else ""
            print(
                f"  header@{hex(header_pos)} nb_verts={nb_verts} nb_idx={nb_idx} "
                f"-> vbuf@{hex(vbuf_start)}{vif_tag} ({len(vertices)} verts) | "
                f"strategie={strategy_name} offset={off} | {g}/{t} coherents ({ratio*100:.0f}%)"
            )

        if best_overall is None or g > best_overall[0]:
            best_overall = (g, t, result, vertices, strategy_name, off, idxs)

    if best_overall is None:
        raise ValueError(f"{path}: aucune strategie de decodage n'a fonctionne")

    g, t, result, vertices, strategy_name, off, idxs = best_overall

    if strategy_name == "groupes de 6":
        # meme logique que le scoring : chaque triangle du quad teste independamment
        scale = local_scale(vertices)
        diag_thresh = 6.0 * scale
        planar_thresh = 1.0 * scale
        tris = []
        for A, B, C, D in result:
            t1, t2 = _quad_triangle_accept(A, B, C, D, vertices, diag_thresh, planar_thresh)
            if t1 and len({A, B, C}) == 3:
                tris.append((A, B, C))
            if t2 and len({B, D, C}) == 3:
                tris.append((B, D, C))
    else:
        # Filtre CIBLE : seuls les triangles emis juste apres un rebond de
        # degenerescence (changement de direction dans le strip) sont
        # suspects -- ce sont eux qui peuvent sauter un sommet-charniere et
        # relier deux parties non adjacentes de l'objet (observe sur gim127
        # et gim136). On ne touche pas aux autres triangles, meme s'ils ont
        # de grandes aretes (une planche plate est une geometrie legitime,
        # pas un artefact) -- contrairement a un filtre global par taille
        # d'arete qui supprimait aussi les vraies grandes planches.
        tris_flagged, flags = strip_decode_flagged(idxs, len(vertices), off)

        # Echelle LOCALE par sommet plutot que globale : un objet complexe
        # (mats fins + coque large) a une densite tres variable, une valeur
        # unique pour tout l'objet casse soit les zones fines soit laisse
        # passer des ponts dans les zones larges.
        nn_dist = per_vertex_nn_distance(vertices)

        # Seuil ADAPTATIF par detection du plus grand "saut" dans la
        # distribution des ratios (arete / echelle locale) des triangles
        # suspects : en pratique la geometrie legitime (dents, details fins)
        # forme une rampe continue jusqu'a un certain ratio, puis les vrais
        # ponts parasites sautent brutalement bien au-dessus. Un facteur FIXE
        # (ex: x4) coupe a tort toute une zone de detail legitime des que son
        # echelle differe un peu du reste (observe sur une epee : les dents
        # de la garde etaient sabrees alors qu'aucun vrai pont n'existait
        # avant un saut a x35). On ne rejette donc que ce qui depasse le
        # saut, pas un multiple arbitraire de l'echelle.
        bounce_ratios = []
        for (A, B, C), fl in zip(tris_flagged, flags):
            if fl != 1:
                continue
            pA, pB, pC = vertices[A], vertices[B], vertices[C]
            e = max(dist(pA, pB), dist(pB, pC), dist(pC, pA))
            local_ref = (nn_dist[A] + nn_dist[B] + nn_dist[C]) / 3.0
            if local_ref > 0:
                bounce_ratios.append(e / local_ref)

        bounce_factor = 6.0  # repli si pas assez de donnees pour detecter un saut fiable
        if len(bounce_ratios) >= 5:
            srt = sorted(bounce_ratios)
            best_gap_ratio = 1.0
            best_gap_idx = None
            start = max(1, len(srt) // 2)  # ignorer la moitie basse, jamais un vrai "saut" la
            for i in range(start, len(srt) - 1):
                if srt[i] <= 0:
                    continue
                gap_ratio = srt[i + 1] / srt[i]
                if gap_ratio > best_gap_ratio:
                    best_gap_ratio = gap_ratio
                    best_gap_idx = i
            if best_gap_idx is not None and best_gap_ratio > 1.5 and srt[best_gap_idx] <= 20.0:
                bounce_factor = srt[best_gap_idx] * 1.05  # juste au-dessus du dernier point "normal"
            # sinon : pas de saut net dans une plage raisonnable (bruit et
            # signal melanges jusqu'a un ratio deja enorme, ex: gim127, ou le
            # "saut" ne separe qu'un unique cas pathologique a des centaines
            # de fois l'echelle du reste) -> repli prudent fixe (6.0).

        # Fermeture de boucle : seuil global par rapport a la plus grande
        # arete deja validee (comme precedemment, ca marchait bien pour
        # detecter la diagonale pole-a-pole de gim136).
        base_max_edge = 0.0
        for (A, B, C), fl in zip(tris_flagged, flags):
            if fl == 0:
                pA, pB, pC = vertices[A], vertices[B], vertices[C]
                base_max_edge = max(base_max_edge, dist(pA, pB), dist(pB, pC), dist(pC, pA))
        wrap_thresh = base_max_edge * 1.2

        tris = []
        for (A, B, C), fl in zip(tris_flagged, flags):
            if fl == 0:
                tris.append((A, B, C))
                continue
            pA, pB, pC = vertices[A], vertices[B], vertices[C]
            e = max(dist(pA, pB), dist(pB, pC), dist(pC, pA))
            if fl == 2:
                if e <= wrap_thresh:
                    tris.append((A, B, C))
            else:
                local_ref = (nn_dist[A] + nn_dist[B] + nn_dist[C]) / 3.0
                if local_ref <= 0 or e <= local_ref * bounce_factor:
                    tris.append((A, B, C))

    tris = orient_outward(tris, vertices)

    with open(out_obj, "w") as f:
        f.write(f"# {os.path.basename(path)} - decode automatiquement ({strategy_name}, offset={off})\n")
        for v in vertices:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for A, B, C in tris:
            f.write(f"f {A+1} {B+1} {C+1}\n")

    if verbose:
        print(f"  >>> RETENU : {strategy_name}, offset={off}, {len(vertices)} verts, {len(tris)} triangles -> {out_obj}\n")

    return len(vertices), len(tris)


def batch_process(root, out_dir, verbose=False):
    """Traite tous les .ddm/.bin d'un dossier (recursif), avec rapport final."""
    root = Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = [p for p in root.rglob("*") if p.is_file()]
    ddm_files = []
    for p in candidates:
        try:
            with open(p, "rb") as f:
                if f.read(4) == MAGIC:
                    ddm_files.append(p)
        except Exception:
            continue

    print(f"{len(ddm_files)} fichiers .ddm trouves dans {root}\n")

    import concurrent.futures as cf

    results = []
    TIMEOUT_SECONDS = 30
    with cf.ProcessPoolExecutor(max_workers=1) as executor:
        for p in ddm_files:
            out_name = out_dir / (p.stem + "_auto.obj")
            future = executor.submit(decode_ddm, str(p), str(out_name), False)
            try:
                n_verts, n_tris = future.result(timeout=TIMEOUT_SECONDS)
                results.append((p, "OK", n_verts, n_tris))
                if verbose:
                    print(f"[ok] {p.name}: {n_verts} verts, {n_tris} tris")
            except cf.TimeoutError:
                future.cancel()
                # le worker precedent est bloque -> on redemarre un pool frais
                executor.shutdown(wait=False, cancel_futures=True)
                executor = cf.ProcessPoolExecutor(max_workers=1)
                results.append((p, "ERREUR: timeout (>30s), fichier saute", 0, 0))
                if verbose:
                    print(f"[timeout] {p.name}: saute apres {TIMEOUT_SECONDS}s")
            except Exception as e:
                results.append((p, f"ERREUR: {e}", 0, 0))
                if verbose:
                    print(f"[fail] {p.name}: {e}")

    ok = [r for r in results if r[1] == "OK"]
    failed = [r for r in results if r[1] != "OK"]

    report_path = out_dir / "_rapport.txt"
    with open(report_path, "w") as f:
        f.write(f"Rapport de decodage DDM en lot\n")
        f.write(f"Total: {len(results)}  OK: {len(ok)}  Echecs: {len(failed)}\n\n")
        f.write("=== ECHECS ===\n")
        for p, status, _, _ in failed:
            f.write(f"{p}: {status}\n")
        f.write("\n=== OK ===\n")
        for p, status, nv, nt in ok:
            f.write(f"{p}: {nv} verts, {nt} tris\n")

    print(f"\nTermine : {len(ok)}/{len(results)} decodes avec succes ({len(failed)} echecs)")
    print(f"Rapport complet : {report_path}")
    print(f"OBJ generes dans : {out_dir}")
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Decodeur DDM generique -> OBJ")
    ap.add_argument("input", nargs="+", help="Fichier(s) .ddm/.bin, ou un dossier avec --batch")
    ap.add_argument("--batch", action="store_true", help="Traiter l'entree comme un dossier (recursif)")
    ap.add_argument("--out", default=".", help="Dossier de sortie (mode --batch uniquement)")
    args = ap.parse_args()

    if args.batch:
        batch_process(args.input[0], args.out, verbose=True)
    else:
        for fname in args.input:
            try:
                print(f"=== {fname} ===")
                out = os.path.splitext(fname)[0] + "_auto.obj"
                decode_ddm(fname, out)
            except Exception as e:
                print(f"  ERREUR : {e}\n")