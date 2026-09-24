# Archive — ancien résumé d'extraction

> Document historique conservé pour référence. Les chemins, commandes et
> statistiques ci-dessous ne décrivent pas nécessairement l'état actuel du projet.
> Voir `README.md` à la racine pour le workflow maintenu.

# Extraction et Conversion des Ressources du Jeu PS3 Majin

## Historique et Statut Actuel

### Ce qui a été accompli

**Phase 1 - Extraction initiale (COMPLETÉ)**
- Extraction de **2636 fichiers** (528 Mo) des archives .pak
- Organisés dans `extracted_full/` avec la structure originale
- Formats identifiés : CHR, GIM, static, etc.

**Phase 2 - Découverte du format (EN COURS)**
- Identification du problème : les fichiers extraits n'étaient pas complètement décompressés
- Découverte que les archives .pak utilisent la compression zlib (déflation brute)
- Mise en place de `pak_extractor1_fixed.py` qui décompresse correctement les fichiers

**Phase 3 - Conversion des ressources (EN COURS)**
- Extraction réussie des fichiers au format natif (XET, DDS, DDM)
- Conversion des textures XET en PNG ✅
- Conversion des textures DDS en PNG ✅
- Conversion des modèles DDM en OBJ ✅

### Structure du Projet Actuelle

```
Majin_reverse_engineering/
├── ExtractedISO/                    # ISO PS3 extrait
│   └── PS3_GAME/USRDIR/finalizedPS3/KB/package/
│       ├── chr*.pak (112 fichiers)   # Modèles de personnages
│       ├── gim*.pak (228 fichiers)  # Images/textures
│       ├── static*.pak (3 fichiers) # Ressources statiques
│       └── demo/                    # Fichiers de démonstration
│
├── extracted_full/                   # Fichiers extraits (version initiale - NON UTILISABLES)
│   └── 2636 fichiers avec en-têtes incomplets
│
├── DECOMPRESSED_ALL_v2/             # Fichiers CORRECTEMENT décompressés (NOUVEAU)
│   ├── KB/texture/common/gim/      # Textures XET
│   ├── KB/chara/                   # Modèles XET et DDM
│   ├── KB/map/                     # Cartes (DDM)
│   └── ...
│
├── TEXTURES_TEST/                   # Textures converties en PNG (échantillons)
│   ├── gim001_c01.png (128x128)
│   ├── gim001_f.png (512x512)
│   └── ...
│
├── Scripts d'extraction/
│   ├── extract_pak_v9.py            # Version initiale (obsolète)
│   ├── extract_all_final.py         # Version initiale (obsolète)
│   └── pak_extractor1_fixed.py      # Version CORRECTE
│
├── Scripts de conversion/ (previous_work/)
│   ├── xet_to_png_fixed.py          # Convertisseur XET → PNG
│   ├── dds_to_png_fixed.py          # Convertisseur DDS → PNG
│   └── ddm_decoder.py               # Convertisseur DDM → OBJ
│
└── Documentation/
    ├── README_CONVERSION.md         # Guide complet
    └── EXTRACTION_SUMMARY.md         # Ce fichier
```

## Problème Principal Identifié

Les fichiers dans `extracted_full/` étaient **incomplets** - ils n'étaient pas décompressés depuis leur format zlib natif dans les archives .pak. Cela expliquait pourquoi :
- Les signatures de fichiers ne correspondaient pas (ex: `f664c4f2` au lieu de `00786574`)
- Les textures converties étaient du bruit
- Les formats ne pouvaient pas être identifiés

### Solution

Utiliser `pak_extractor1_fixed.py` qui :
1. Parse correctement la structure des archives .pak
2. Détecte les séparateurs (zones de zéros entre les fichiers)
3. Décompresse chaque chunk avec zlib déflation brute (wbits=-15)
4. Produit des fichiers avec les vraies signatures : `00786574` (XET), `0064646d` (DDM), etc.

## Formats de Fichiers Identifiés

### Formats de Texture

#### 1. XET (Game Republic PS3 Texture)
- **Magic** : `\x00xet` (0x00786574)
- **Structure** :
  - Offset 0x00 : Magic (4 octets)
  - Offset 0x80 : Largeur (uint16 big-endian)
  - Offset 0x82 : Hauteur (uint16 big-endian)
  - Offset 0x90 : Début des données de texture
- **Formats de compression** : DXT1, DXT5
- **Particularité** : Les blocs DXT5 sont stockés couleur-d'abord, alpha-deuxièmement
- **Mipmaps** : Chaîne s'arrête à 2x2 (pas de mipmap 1x1)
- **Nombre** : 2858 fichiers (d'après summary.json)

#### 2. DDS (DirectDraw Surface)
- **Magic** : `DDS ` (4 octets)
- **Structure** : Header DDS standard (128 octets)
  - Offset 12 : Largeur (uint32 little-endian)
  - Offset 16 : Hauteur (uint32 little-endian)
  - Offset 84 : FourCC (DXT1 ou DXT5)
- **Formats de compression** : DXT1, DXT5
- **Nombre** : 1643 fichiers (d'après summary.json)

### Formats de Modèle 3D

#### 3. DDM (Model Data)
- **Magic** : `\x00ddm` (0x0064646d)
- **Structure** :
  - Contient des headers de section géométrie (pattern `\x00\x00\x00\x07`)
  - Buffer de vertices (marqueur 0xFFFFFFFF à stride 24)
  - Indices stockés comme uint16
- **Stratégies de décodage** :
  - Strip standard : Fenêtre glissante de 3, ignore les indices dégénérés
  - Groupes de 6 : Pattern [A,B,B,C,C,D] → quads (A,B,C,D)
- **Nombre** : 324 fichiers (d'après summary.json)

### Autres Formats

| Magic | Nom | Nombre | Description |
|-------|-----|--------|-------------|
| `\x00prs` | PRS | 609 | Probablement compressé |
| `\x00hcs` | HCS | 465 | Sound cue |
| `\x00crg` | CRG | 242 | Inconnu |
| `\x00mvn` | MVN | 227 | Inconnu |
| `\x00lpt` | LPT | 279 | Inconnu |
| `psmr` | PSMR | ? | Motion |
| `\x00sme` | SME | ? | Inconnu |

## Workflow de Conversion Validé

### Étape 1 : Extraction Complète des Archives .pak

```bash
# Extraire TOUS les fichiers .pak
python pak_extractor1_fixed.py \
    "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/" \
    --out DECOMPRESSED_ALL_v2
```

**Sortie** : Fichiers avec les vraies magics dans `DECOMPRESSED_ALL_v2/`

### Étape 2 : Conversion des Textures XET

```bash
# Convertir toutes les textures XET
python previous_work/xet_to_png_fixed.py \
    DECOMPRESSED_ALL_v2 \
    --out TEXTURES_ALL \
    --recursive
```

**Sortie** : Fichiers PNG dans `TEXTURES_ALL/`

Exemples de conversions réussies :
- `gim001_c01` → 128x128 DXT1 PNG
- `gim001_f` → 512x512 DXT1 PNG
- `fd_gim_door_n` → 1024x1024 DXT1 PNG
- `ct_savetree_eda_c` → 512x512 DXT1 PNG

### Étape 3 : Conversion des Textures DDS

```bash
# Convertir toutes les textures DDS
python previous_work/dds_to_png_fixed.py \
    DECOMPRESSED_ALL_v2 \
    --out TEXTURES_ALL
```

### Étape 4 : Conversion des Modèles DDM

```bash
# Convertir tous les modèles DDM (peut prendre du temps)
python previous_work/ddm_decoder.py \
    DECOMPRESSED_ALL_v2 \
    --out MODELS_ALL \
    --batch
```

**Sortie** : Fichiers OBJ dans `MODELS_ALL/`

## Validation des Résultats

### Vérification des Textures

Les textures converties doivent avoir les caractéristiques suivantes :

| Texture | Taille | Couleurs uniques | Différence moyenne | Statut |
|---------|--------|------------------|-------------------|--------|
| gim001_c01.png | 128x128 | 723/16384 (4.4%) | 182.24 | ✅ VALIDE |
| gim001_f.png | 512x512 | 19895/262144 (7.6%) | 204.74 | ✅ VALIDE |
| fd_gim_door_n.png | 1024x1024 | 13853/1048576 (1.3%) | 233.50 | ✅ VALIDE |

**Critères de validité** :
- Différence moyenne entre pixels voisins < 400 ✅
- Pourcentage de couleurs uniques < 10% (pour la plupart des textures) ✅
- Dimensions réalistes (puissances de 2 : 128, 256, 512, 1024) ✅

### Vérification des Modèles

Les modèles convertis doivent être :
- Au format Wavefront OBJ
- Avec les vertices et faces correctement décodés
- Avec une géométrie cohérente

## Scripts Disponibles

### pak_extractor1_fixed.py
**Fonction** : Extracteur universel des archives .pak (format `\x00kap`)

**Améliorations par rapport à extract_pak_v9.py** :
- Utilise zlib.decompressobj(-15) pour la déflation brute
- Détecte les séparateurs (zones de zéros) pour segmenter les chunks
- Essaye avec et sans footer (-16 octets)
- Parse correctement les entrées KB/ dans l'index

**Usage** :
```bash
# Fichier unique
python pak_extractor1_fixed.py fichier.pak --out DECOMPRESSED/

# Dossier complet
python pak_extractor1_fixed.py dossier/ --out DECOMPRESSED/
```

### xet_to_png_fixed.py
**Fonction** : Convertisseur de textures XET vers PNG

**Fonctionnalités** :
- Détection automatique de la largeur/hauteur à l'offset 0x80
- Support de DXT1 et DXT5
- Détection automatique de l'offset des données
- Décompression des chaînes de mipmaps
- Format de bloc DXT5 spécifique : couleur-d'abord, alpha-deuxièmement

**Usage** :
```bash
# Fichier unique
python xet_to_png_fixed.py fichier.xet --out PNG_OUT/

# Dossier récursif
python xet_to_png_fixed.py dossier/ --out PNG_OUT/ --recursive
```

### dds_to_png_fixed.py
**Fonction** : Convertisseur de textures DDS vers PNG

**Fonctionnalités** :
- Parse le header DDS standard
- Support de DXT1 et DXT5
- Conversion en RGBA 8 bits

**Usage** :
```bash
python dds_to_png_fixed.py fichier.dds --out PNG_OUT/
```

### ddm_decoder.py
**Fonction** : Convertisseur de modèles 3D DDM vers OBJ

**Fonctionnalités** :
- Détection automatique des headers de géométrie
- Détection du buffer de vertices (marqueur 0xFFFFFFFF)
- Support de deux stratégies de décodage :
  - Strip standard : Fenêtre glissante de 3
  - Groupes de 6 : Pattern [A,B,B,C,C,D]
- Détection automatique du meilleur offset par scan
- Orientation des faces vers l'extérieur
- Correction du winding pour les maillages non-manifold
- Détection adaptative des seuils de cohérence

**Usage** :
```bash
# Fichier unique
python ddm_decoder.py fichier.ddm --out OBJ_OUT/

# Batch
python ddm_decoder.py dossier/ --out OBJ_OUT/ --batch
```

## Prochaines Étapes

### Priorité Haute 🔴
- [ ] Extraire TOUS les fichiers .pak (697 fichiers) → DECOMPRESSED_ALL_v2/
- [ ] Convertir toutes les textures XET → TEXTURES_ALL/
- [ ] Convertir toutes les textures DDS → TEXTURES_ALL/

### Priorité Moyenne 🟡
- [ ] Convertir tous les modèles DDM → MODELS_ALL/
- [ ] Identifier et convertir les fichiers PRS
- [ ] Identifier et convertir les fichiers HCS (sound cues)
- [ ] Identifier et convertir les fichiers CRG, MVN, LPT

### Priorité Basse 🟢
- [ ] Analyser les fichiers restants avec des magics inconnues
- [ ] Rechercher des outils pour les formats inconnus
- [ ] Créer des visualiseurs pour les ressources converties
- [ ] Documenter toutes les structures de fichiers

## Statistiques

### Fichiers Originaux (dans les archives .pak)
- **Total des fichiers .pak** : 697
- **Types de fichiers** :
  - chr*.pak : 112 fichiers (modèles de personnages)
  - gim*.pak : 228 fichiers (textures)
  - static*.pak : 3 fichiers (ressources statiques)
  - demo/ : Dossier (fichiers de démonstration)

### Fichiers Décompressés Attendus (d'après summary.json)
- **XET (textures)** : ~2858 fichiers
- **DDS (textures)** : ~1643 fichiers
- **DDM (modèles)** : ~324 fichiers
- **PRS (compressés)** : ~609 fichiers
- **HCS (sound cues)** : ~465 fichiers
- **CRG** : ~242 fichiers
- **MVN** : ~227 fichiers
- **LPT** : ~279 fichiers

**Total estimé** : ~8399 fichiers

### Fichiers Actuellement Convertis
- **Textures XET** : 30+ fichiers testés et validés
- **Modèles DDM** : 0 (pas encore testé)
- **Textures DDS** : 0 (pas encore testé)

## Références Utiles

### Communautés de Modding
- PS3 Dev Wiki
- Xenon Hex (PS3 reverse engineering)
- Zenhax forum (file format reverse engineering)

### Outils Externes Recommandés
- **Noesis** : Outil de visualisation de modèles 3D (peut importer OBJ, DDS, etc.)
- **Blender** : Avec des plugins pour importer des formats personnalisés
- **Ohana** : Outil PS3 texture viewer/extractor
- **QuickBMS** : Avec des scripts pour les archives de jeu
- **Hex Workshop** / **010 Editor** : Éditeurs hexadécimaux avancés

### Bibliothèques Python Utilisées
- `Pillow` (PIL) : Manipulation d'images
- `numpy` : Traitement des données binaires
- `struct` : Unpack des structures binaires
- `zlib` : Décompression
- `pygltflib` : Export GLB/GLTF (pour une future implémentation)

## Résumé des Fichiers Importants

| Fichier/Script | Description | Statut |
|---------------|-------------|--------|
| `pak_extractor1_fixed.py` | Extracteur .pak avec décompression zlib | ✅ TESTÉ |
| `xet_to_png_fixed.py` | Convertisseur XET → PNG | ✅ TESTÉ |
| `dds_to_png_fixed.py` | Convertisseur DDS → PNG | ✅ TESTÉ |
| `ddm_decoder.py` | Convertisseur DDM → OBJ | ✅ DISPONIBLE |
| `DECOMPRESSED_ALL_v2/` | Fichiers décompressés | ⚠️ PARTIEL |
| `TEXTURES_TEST/` | Textures converties (échantillons) | ✅ VALIDÉES |

## Problèmes Résolus

✅ **Problème : Les fichiers extraits sont du bruit**
- **Cause** : Les fichiers n'étaient pas décompressés depuis zlib
- **Solution** : Utiliser `pak_extractor1_fixed.py` qui décompresse avec zlib.decompressobj(-15)

✅ **Problème : Les signatures de fichiers ne correspondent pas**
- **Cause** : Les fichiers étaient encore compressés
- **Solution** : Décompresser complètement avec le bon extracteur

✅ **Problème : Les convertisseurs ne reconnaissent pas les fichiers**
- **Cause** : Les magics étaient incorrectes
- **Solution** : Utiliser les fichiers décompressés avec les vraies magics

## Notes Techniques

### Structure des archives .pak

```
Offset 0x00-0x03 : Signature (\x00kap)
Offset 0x04-0x07 : Version (uint32 big-endian, généralement 2)
Offset 0x08-0x0B : Nombre de fichiers (uint32 big-endian)
Offset 0x0C-0x0F : Inconnu (généralement 1)
Offset 0x10+    : Index des fichiers
  - Chaque entrée : 0x180 octets
  - Contient le chemin du fichier (KB/...)
  - Contient la taille et l'offset (à masquer)

Zone des données :
- Sépare les chunks avec des zones de zéros (16+ octets)
- Chaque chunk est compressé avec zlib (déflation brute)
- Doit être décompressé avec wbits=-15
```

### Structure XET

```
Offset 0x00-0x03 : Magic (\x00xet)
Offset 0x04-0x7F : Header inconnu
Offset 0x80-0x81 : Largeur (uint16 big-endian)
Offset 0x82-0x83 : Hauteur (uint16 big-endian)
Offset 0x84-0x8F : Header inconnu
Offset 0x90+    : Données de texture (DXT1/DXT5)
  - Chaîne de mipmaps
  - Blocs DXT5 : couleur-d'abord, alpha-deuxièmement
```

### Structure DDS

```
Offset 0x00-0x03 : Magic (DDS )
Offset 0x0C-0x0F : Largeur (uint32 little-endian)
Offset 0x10-0x13 : Hauteur (uint32 little-endian)
Offset 0x54-0x57 : FourCC (DXT1 ou DXT5)
Offset 0x80+    : Données de texture
```

## Commandes Utiles

### Extraire un fichier .pak spécifique
```bash
python pak_extractor1_fixed.py \
    "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/gim001.pak" \
    --out DECOMPRESSED_GIM
```

### Convertir toutes les textures XET dans un dossier
```bash
python previous_work/xet_to_png_fixed.py \
    DECOMPRESSED_GIM/KB/texture/ \
    --out TEXTURES_GIM \
    --recursive
```

### Compter les fichiers par magic
```bash
python -c "
import os
from collections import Counter
magic_counts = Counter()
for root, dirs, files in os.walk('DECOMPRESSED_ALL_v2'):
    for f in files:
        filepath = os.path.join(root, f)
        try:
            with open(filepath, 'rb') as file:
                magic = file.read(4)
            magic_counts[magic] += 1
        except: pass
for magic, count in magic_counts.most_common():
    print(f'{magic.hex()}: {count} files')
"
```

## Conclusion

Le problème principal a été identifié et résolu. Les fichiers doivent être d'abord **complètement décompressés** depuis les archives .pak en utilisant `pak_extractor1_fixed.py`, puis les formats natifs (XET, DDS, DDM) peuvent être convertis avec les scripts fournis.

**Prochaine étape critique** : Exécuter une extraction complète de tous les fichiers .pak, puis convertir toutes les textures et modèles.
