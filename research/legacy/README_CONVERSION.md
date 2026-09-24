# Archive — legacy conversion guide

> Document historique conservé pour les résultats de recherche. Les commandes et
> noms de scripts ci-dessous peuvent être obsolètes. Voir `README.md` à la racine
> pour le workflow actuel.

# Conversion des ressources du jeu PS3 Majin

## Résumé des progrès

Nous avons identifié et résolu le problème principal : les fichiers extraits précédemment (`extracted_full/`) n'étaient pas complètement décompressés. Les archives .pak du jeu contiennent des fichiers compressés avec zlib (déflation brute) qui doivent être décompressés avant la conversion.

### Résultats obtenus

✅ **Fonctionnel** :
- Extraction complète des fichiers .pak avec décompression zlib
- Conversion des textures XET en PNG (DXT1/DXT5)
- Conversion des textures DDS en PNG
- Détection des formats : XET (`\x00xet`), DDS (`DDS `), DDM (`\x00ddm`), etc.

### Fichiers extraits et convertis

1. **DECOMPRESSED_ALL_v2/** - Fichiers décompressés depuis les archives .pak
   - Contient les vrais fichiers au format XET, DDS, DDM, etc.
   - Magic correctes : `00786574` (XET), `0064646d` (DDM), `00637267` (CRG), etc.

2. **TEXTURES_TEST/** - Textures converties depuis gim001.pak
   - Exemples : gim001_c01.png (128x128), gim001_f.png (512x512), fd_gim_door_n.png (1024x1024)
   - Toutes validées comme vraies textures (non du bruit)

## Workflow de conversion

### Étape 1 : Extraire les fichiers .pak

```bash
# Extraire tous les fichiers .pak
python pak_extractor1_fixed.py "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/" --out DECOMPRESSED_ALL_v2
```

Ce script :
- Parse les entrées KB/ dans l'index des fichiers .pak
- Trouve les séparateurs (zones de zéros)
- Décompresse chaque chunk avec zlib déflation brute (wbits=-15)
- Sauvegarde les fichiers avec leurs noms originaux

### Étape 2 : Convertir les textures XET en PNG

```bash
# Convertir un fichier unique
python previous_work/xet_to_png_fixed.py "DECOMPRESSED_ALL_v2/path/to/file" --out TEXTURES

# Convertir un dossier récursivement
python previous_work/xet_to_png_fixed.py "DECOMPRESSED_ALL_v2/KB/texture/" --out TEXTURES --recursive
```

Format XET :
- Magic : `\x00xet`
- Largeur/Hauteur à l'offset 0x80 (big-endian u16)
- Données de texture commencent à 0x90
- Formats supportés : DXT1, DXT5
- Arrêt de la chaîne de mipmaps à 2x2

### Étape 3 : Convertir les textures DDS en PNG

```bash
# Convertir un fichier unique
python previous_work/dds_to_png_fixed.py "DECOMPRESSED_ALL_v2/path/to/file.dds" --out TEXTURES

# Convertir un dossier
python previous_work/dds_to_png_fixed.py "DECOMPRESSED_ALL_v2/KB/flash/" --out TEXTURES
```

Format DDS :
- Magic : `DDS ` (4 octets)
- Largeur/Hauteur dans le header DDS (offset 12, 16)
- Pixel format : DXT1 ou DXT5 (offset 84-88)
- Données commencent après le header de 128 octets

### Étape 4 : Convertir les modèles 3D DDM en OBJ

```bash
# Convertir un fichier unique
python previous_work/ddm_decoder.py "DECOMPRESSED_ALL_v2/path/to/file.ddm" --out MODELS

# Traiter un dossier en batch
python previous_work/ddm_decoder.py "DECOMPRESSED_ALL_v2/KB/map/" --out MODELS --batch
```

Format DDM :
- Magic : `\x00ddm`
- Contient la géométrie des modèles 3D
- Détecte automatiquement :
  - Headers de section géométrie (pattern `\x00\x00\x00\x07`)
  - Buffer de vertices (marqueur 0xFFFFFFFF à stride 24)
  - Stratégie de décodage des indices (strip standard ou groupes de 6)
  - Meilleur offset par scan systématique
- Sortie : Fichier OBJ avec vertices et faces

## Formats de fichiers identifiés

D'après le `summary.json` du travail précédent :

### Textures
- **XET** (`\x00xet`) : 2858 fichiers - Format propriétaire Game Republic PS3
- **DDS** (`DDS `) : 1643 fichiers - Format standard DirectDraw Surface
- **DDM** (`\x00ddm`) : 324 fichiers - Modèles 3D

### Autres formats
- **PRS** (`\x00prs`) : 609 fichiers - Probablement compressé
- **HCS** (`\x00hcs`) : 465 fichiers - Sound cue
- **CRG** (`\x00crg`) : 242 fichiers - Inconnu
- **MVN** (`\x00mvn`) : 227 fichiers - Inconnu
- **LPT** (`\x00lpt`) : 279 fichiers - Inconnu
- **SME** (`\x00sme`) : Inconnu

## Scripts disponibles

### pak_extractor1_fixed.py
Extracteur universel .pak pour le format `\x00kap` (Game Republic PS3).

**Fonctionnalités** :
- Détection automatique des entrées KB/
- Détection des séparateurs (zones de zéros)
- Décompression zlib (déflation brute, wbits=-15)
- Prend en charge les fichiers avec ou sans footer

**Usage** :
```bash
python pak_extractor1_fixed.py fichier.pak --out DECOMPRESSED/
python pak_extractor1_fixed.py dossier/ --out DECOMPRESSED/
```

### xet_to_png_fixed.py
Convertisseur de textures XET vers PNG.

**Fonctionnalités** :
- Détection automatique des dimensions (offset 0x80)
- Prend en charge DXT1 et DXT5
- Gestion des mipmaps
- Détection de l'offset des données de texture

**Usage** :
```bash
python xet_to_png_fixed.py fichier.xet --out PNG_OUT/
python xet_to_png_fixed.py dossier/ --out PNG_OUT/ --recursive
```

### dds_to_png_fixed.py
Convertisseur de textures DDS vers PNG.

**Fonctionnalités** :
- Parse le header DDS standard
- Prend en charge DXT1 et DXT5
- Convertit en RGBA 8 bits

**Usage** :
```bash
python dds_to_png_fixed.py fichier.dds --out PNG_OUT/
python dds_to_png_fixed.py dossier/ --out PNG_OUT/
```

### ddm_decoder.py
Convertisseur de modèles 3D DDM vers OBJ.

**Fonctionnalités** :
- Détection automatique des headers de géométrie
- Détection du buffer de vertices
- Prend en charge plusieurs stratégies de décodage des indices
- Détection automatique du meilleur offset
- Orientation des faces vers l'extérieur
- Sortie au format Wavefront OBJ

**Usage** :
```bash
python ddm_decoder.py fichier.ddm --out OBJ_OUT/
python ddm_decoder.py dossier/ --out OBJ_OUT/ --batch
```

## Recommandations

1. **Pour une conversion complète** :
   ```bash
   # Étape 1 : Extraire tous les fichiers
   python pak_extractor1_fixed.py "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/" --out DECOMPRESSED_ALL_v2
   
   # Étape 2 : Convertir toutes les textures XET
   python previous_work/xet_to_png_fixed.py DECOMPRESSED_ALL_v2 --out TEXTURES_ALL --recursive
   
   # Étape 3 : Convertir toutes les textures DDS
   python previous_work/dds_to_png_fixed.py DECOMPRESSED_ALL_v2 --out TEXTURES_ALL
   
   # Étape 4 : Convertir tous les modèles DDM
   python previous_work/ddm_decoder.py DECOMPRESSED_ALL_v2 --out MODELS_ALL --batch
   ```

2. **Pour tester sur un sous-ensemble** :
   ```bash
   # Extraire seulement les fichiers gim*.pak
   for f in gim*.pak; do
       python pak_extractor1_fixed.py "ExtractedISO/PS3_GAME/USRDIR/finalizedPS3/KB/package/$f" --out DECOMPRESSED_GIM
   done
   
   # Convertir les XET extraits
   python xet_to_png_fixed.py DECOMPRESSED_GIM --out TEXTURES_GIM --recursive
   ```

3. **Vérification de la qualité** :
   Les textures converties doivent avoir :
   - Une différence moyenne entre les pixels voisins < 400
   - Un pourcentage de couleurs uniques < 10% (pour la plupart des textures)
   - Des dimensions réalistes (128x128, 256x256, 512x512, 1024x1024)

## Problèmes connus et solutions

### Problème : Les fichiers PNG convertis sont du bruit
**Cause** : Les fichiers n'étaient pas complètement décompressés depuis les archives .pak.
**Solution** : Utiliser `pak_extractor1_fixed.py` au lieu de `extract_pak_v9.py`.

### Problème : Caractères Unicode dans la sortie
**Cause** : Certains scripts utilisent des caractères Unicode (✓) qui ne sont pas supportés dans la console Windows.
**Solution** : Utiliser les versions "fixed" des scripts ou rediriger la sortie vers un fichier.

### Problème : Fichiers très grands (maps)
**Cause** : Certains fichiers DDM peuvent avoir des milliers de vertices et prendre du temps à convertir.
**Solution** : Le script `ddm_decoder.py` a un timeout de 30 secondes par fichier par défaut.

## Résultats attendus

Avec ce workflow, vous devriez obtenir :
- **Des milliers de textures** au format PNG (128x128 à 1024x1024)
- **Des centaines de modèles 3D** au format OBJ
- **D'autres ressources** dans leurs formats respectifs (DDS, CRG, PRS, etc.)

## Statistiques

D'après le `summary.json` du travail précédent sur un jeu similaire :
- 2858 fichiers XET (textures)
- 1643 fichiers DDS (textures)
- 324 fichiers DDM (modèles 3D)
- 609 fichiers PRS (compressés)
- 465 fichiers HCS (sound cues)
- 242 fichiers CRG (inconnus)
- 227 fichiers MVN (inconnus)
- 279 fichiers LPT (inconnus)

Total : 8399 fichiers analysés.

## Notes

- Les fichiers XET utilisent une variant de DXT où les blocs sont stockés couleur-d'abord, alpha-deuxièmement
- Les fichiers DDM peuvent avoir différents schémas d'encodage des indices (strip standard ou groupes de 6)
- Certains fichiers peuvent nécessiter des offsets de remappage pour corriger les indices
- Le décodeur DDM sélecte automatiquement la meilleure stratégie et le meilleur offset
