# Configuration VSCode pour le projet Majin Reverse Engineering

Ce dossier contient les configurations pour exécuter et déboguer les scripts Python directement depuis Visual Studio Code.

## 📁 Fichiers de Configuration

| Fichier | Description |
|---------|-------------|
| `launch.json` | Configurations de lancement pour exécuter les scripts individuels |
| `tasks.json` | Tâches VSCode pour l'automatisation (pipeline complet) |
| `settings.json` | Paramètres VSCode optimisés pour Python |

---

## 🚀 Utilisation

### **Méthode 1 : Exécuter une configuration individuelle**
1. Ouvrez VSCode dans le projet
2. Allez dans l'onglet **"Exécuter et Déboguer"** (Ctrl+Shift+D)
3. Sélectionnez la configuration souhaitée dans le menu déroulant
4. Cliquez sur le bouton ▶️ **Démarrer le débogage** (F5)

**Configurations disponibles** :
- 📦 **Extraire TOUS les .pak** → Extrait tous les fichiers .pak vers `DECOMPRESSED_ALL_v2/`
- 🎨 **Convertir TOUTES les textures XET** → Convertit les XET en PNG
- 🎨 **Convertir textures DDS** → Convertit les DDS en PNG
- 🗾 **Convertir TOUS les modèles DDM** → Convertit les DDM en OBJ
- 📦➡️🎨 **Pipeline COMPLET** → Exécute les 4 étapes en séquence
- 🔍 **Tester un seul .pak (gim001.pak)** → Pour les tests rapides
- 🎯 **Convertir gims extraits en PNG** → Convertit les fichiers décompressés
- 📊 **Classifier les fichiers par magic** → Analyse les fichiers extraits

---

### **Méthode 2 : Utiliser les tâches VSCode**
1. Ouvrez la palette de commandes (Ctrl+Shift+P)
2. Tapez `Run Task`
3. Sélectionnez la tâche souhaitée

**Tâches disponibles** :
- Toutes les configurations de la Méthode 1
- 🧹 **Nettoyer les dossiers temporaires** → Supprime `DECOMPRESSED_TEST/` et `TEXTURES_TEST/`
- 📊 **Compter les fichiers par type** → Affiche les magics des fichiers

---

### **Méthode 3 : Exécuter le pipeline complet en une seule commande**
1. Ouvrez la palette de commandes (Ctrl+Shift+P)
2. Tapez `Run Task`
3. Sélectionnez **"📦➡️🎨 Pipeline COMPLET (4 étapes)"**

**Ce que fait le pipeline** :
```
Étape 1 : Extraction de tous les .pak → DECOMPRESSED_ALL_v2/
Étape 2 : Conversion des textures XET → TEXTURES_ALL/
Étape 3 : Conversion des textures DDS → TEXTURES_ALL/
Étape 4 : Conversion des modèles DDM → MODELS_ALL/
```

---

## 🔧 Paramètres Recommandés

### **Raccourcis clavier utiles**
| Raccourci | Action |
|-----------|--------|
| `F5` | Démarrer le débogage (exécute la configuration active) |
| `Ctrl+F5` | Exécuter sans débogage |
| `Ctrl+Shift+P` | Ouvrir la palette de commandes |
| `Ctrl+Shift+D` | Ouvrir l'onglet Exécuter/Déboguer |
| `Ctrl+Shift+B` | Exécuter la tâche de build |

### **Personnalisation**
Vous pouvez modifier les chemins dans `launch.json` et `tasks.json` pour :
- Changer les dossiers de sortie
- Ajouter des arguments supplémentaires
- Configurer des variables d'environnement

---

## 📝 Workflow Typique

### **Pour une extraction complète**
1. Sélectionnez **"📦 Extraire TOUS les .pak"** (F5)
2. Attendez la fin de l'extraction (~1h pour 697 fichiers)
3. Sélectionnez **"🎨 Convertir TOUTES les textures XET"** (F5)
4. Sélectionnez **"🎨 Convertir textures DDS"** (F5)
5. Sélectionnez **"🗾 Convertir TOUS les modèles DDM"** (F5)

### **Pour un test rapide**
1. Sélectionnez **"🔍 Tester un seul .pak (gim001.pak)"** (F5)
2. Sélectionnez **"🎯 Convertir gims extraits en PNG"** (F5)
3. Vérifiez les résultats dans `TEXTURES_TEST/`

---

## 💡 Conseils

1. **Console intégrée** : Toutes les configurations utilisent la console intégrée de VSCode pour une meilleure visualisation
2. **Just My Code** : Désactivé pour permettre le débogage des bibliothèques (Pillow, numpy, etc.)
3. **Problèmes de codage** : Certains scripts utilisent des caractères Unicode. VSCode gère cela correctement
4. **Timeout** : Pour les fichiers DDM très grands, le script `ddm_decoder.py` a un timeout de 30s par fichier

---

## 🛠️ Dépannage

### **Problème : La console ne s'affiche pas**
**Solution** : Vérifiez que `"console": "integratedTerminal"` est bien présent dans la configuration

### **Problème : Le script plante avec des erreurs d'encodage**
**Solution** : Les scripts `*_fixed.py` ont été corrigés pour éviter les problèmes d'encodage Windows

### **Problème : Les chemins ne sont pas trouvés**
**Solution** : Vérifiez que vous avez ouvert le bon dossier dans VSCode (le dossier racine du projet)

### **Problème : Je veux modifier les chemins de sortie**
**Solution** : Éditez les arguments `--out` dans les configurations `launch.json`

---

## 📚 Documentation Complète

Pour plus de détails sur le workflow et les scripts, consultez :
- `EXTRACTION_SUMMARY.md` → Résumé complet du projet
- `README_CONVERSION.md` → Guide détaillé de conversion

---

## 🎯 Résumé des Configurations

| Nom | Type | Action | Dossier de sortie |
|-----|------|--------|-------------------|
| Extraire TOUS les .pak | Python | Extraction complète | `DECOMPRESSED_ALL_v2/` |
| Convertir TOUTES les textures XET | Python | Conversion XET → PNG | `TEXTURES_ALL/` |
| Convertir textures DDS | Python | Conversion DDS → PNG | `TEXTURES_ALL/` |
| Convertir TOUS les modèles DDM | Python | Conversion DDM → OBJ | `MODELS_ALL/` |
| Pipeline COMPLET | Shell | 4 étapes en séquence | Plusieurs dossiers |
| Tester gim001.pak | Python | Test rapide | `DECOMPRESSED_TEST/` |
| Convertir gims extraits | Python | Conversion test | `TEXTURES_TEST/` |
