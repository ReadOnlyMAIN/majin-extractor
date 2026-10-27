# Formule originale de KbTarModel (PS3)

Décodage du bytecode local de `static.pak`, 27 octobre 2026.

Les quatre ressources `KbTarModel1` à `KbTarModel4` contiennent chacune huit
passes : **32 programmes vertex et 32 programmes fragment**. Le décodage couvre
leurs instructions, constantes, prédicats, branchements et boucles de skinning.
Les équations ci-dessous sont exécutables dans
`tools/research/tar_shader_math.py`. Les calculs simplifiés de RGBA sont comparés
à un interpréteur séparé des instructions, pour les 32 passes.

Il s'agit des équations du shader, avec ses entrées explicites, pas d'une
reconstitution des valeurs que le code CPU du jeu lui envoie. En particulier,
la conversion des PV en `tarHP` et l'état de mélange du framebuffer ne sont
pas contenus dans ces programmes. L'analyse CPU complémentaire a depuis
identifié les textures globales et certains paramètres par défaut ; voir
[TAR_RENDER_INTEGRATION.md](TAR_RENDER_INTEGRATION.md). Les références numériques utilisent des réels Python ; elles ne
prétendent pas reproduire les arrondis FP16/FMA ou les singularités du RSX bit
pour bit.

## 1. La progression est dans COLOR.r, pas dans la position Y

Le vertex shader écrit :

```text
COL0.rgb = vertex_color.rgb
COL0.a   = vertex_color.a * objectBlendOffset.x
```

Le fragment shader de progression lit **COL0.x**, donc le rouge interpolé et
clampé entre 0 et 1. Il ne compare pas la position Y à un seuil. Les coordonnées
Y interviennent dans l'animation de texture, décrite plus bas.

Pour les passes 0 et 7, soient :

```text
m = clamp(interpolated_vertex_color.r, 0, 1)
x = tarHP.x             # seuil de suppression du fragment
y = tarHP.y             # début de la rampe de transparence
z = tarHP.z             # pente de la rampe
```

La formule exacte en arithmétique réelle est :

```text
if textureSamplerBase(mesh_uv).a < 0.01:
    discard
if m > x:
    discard

alpha = 1
if m > y:
    alpha = 1 - (m - y) * z
```

Le littéral de test alpha est le float32 `0.009999999776482582`. Les comparaisons
sur `m` sont strictes. `tarHP.w` n'est pas lu. L'alpha de la texture de base ne
multiplie pas l'alpha final : il sert uniquement au test de suppression.
Il n'y a **ni smoothstep, ni comparaison directe à Y, ni clamp explicite de la
rampe** dans ce programme. L'état de blending matériel intervient après le shader.

Un couple cohérent de seuils peut satisfaire `x = y + 1/z`, mais le shader ne
l'impose pas : ce sont des paramètres fournis par le moteur. Attribuer à `x`
la formule `1 - PV/PV_max` sans examiner le code CPU serait une supposition.

Les fichiers source confirment un masque préparé sur les sommets. Par exemple,
le rouge de T_body de chr100 est corrélé à Y (environ .866), mais pas égal à une
hauteur normalisée ; T_huku a une corrélation d'environ .816 et une répartition
différente. Le masque suit donc globalement le corps de bas en haut avec des
variations locales, plutôt qu'un plan géométrique horizontal parfait.
Ce masque se déforme avec le personnage ; aucune compensation de root motion
n'est nécessaire pour sa progression. Notre ancien shader Godot de coupure Y
ne reproduisait pas ce mécanisme.

## 2. Vertex shader, skinning et coordonnées du tar

Les matrices `_localMatrixArray` fournissent trois lignes par os. Les passes
pondérées font la somme des quatre transformations, puis normalisent les axes
normal/tangente/binormale. Les passes rigides appliquent une seule matrice.
La position obtenue `P` est dans le même repère que `cameraPos`, `rootPos` et
les lumières. Le shader produit aussi les vecteurs vue, hémisphère et lumières
dans le repère tangent, ainsi que la position projetée.

Les UV ordinaires sont `vertex_uv.xy + uvOffset.xy`. Pour les passes à
projection procédurale, poser :

```text
p = P - rootPos.xyz
n = normalize(skinned_normal)
q = min(abs(n.x), abs(n.z)) / max(abs(n.x), abs(n.z))
t = q*q
```

L'angle est calculé par le polynôme réellement compilé, pas par un atan idéal :

```text
a = (((((-0.01348114013671875*t + 0.057464599609375)*t
          - 0.1212158203125)*t + 0.1956787109375)*t
          - 0.3330078125)*t + 1)*q
if abs(n.z) > abs(n.x): a = 1.5703125 - a
if n.x < 0:            a = 3.140625 - a
if n.z < 0:            a = -a

UVtar.u = 0.012500000186264515*(p.x+p.z)
          + (3.1414999961853027+a)*0.31831926107406616 + uvOffset.z
UVtar.v = p.y*0.03333333507180214 + uvOffset.w
```

Cela combine position relative à la racine, orientation de la normale et
décalage de texture. Les unités source sont conservées : les facteurs sont
approximativement 1/80 et 1/30 dans le corpus DDM centimétrique. Le point
`n.x=n.z=0` est une singularité du quotient ; ses détails numériques relèvent
des instructions réciproques du RSX, pas d'un angle mathématique idéal.
Les passes 3 et 6 utilisent plutôt `vertex_uv.xy` pour les deux paires d'UV.

## 3. Textures et mélange des normales

| Unité | Nom original | Utilisation observée |
| ---: | --- | --- |
| 0 | textureSamplerBase | alpha test ; RGB/alpha aussi dans les passes hybrides 2 et 5 |
| 1 | textureSamplerUtil | intensité spéculaire et reflets |
| 2 | textureSamplerAmbient | rouge utilisé dans deux sinusoïdes de perturbation |
| 3 | textureSamplerNormal | normale détaillée aux UV du modèle |
| 4 | textureSamplerTar | seconde normale aux UV du tar |
| 5 | textureSamplerEnv | environnement cube |
| 6 | textureSamplerEnvSphere | environnement sphérique 2D |
| 7 | textureSamplerShadow | ombre écran de la lumière directionnelle |

Les identités d'assets derrière les samplers globaux ne se déduisent pas de
leurs noms seuls. Notamment, le shader remappe et normalise le RGB de la
texture de tar : il ne l'utilise pas comme un simple masque de transparence.

Avec `M0=matParam0`, `M1=matParam1`, `M2=matParam2`, `UV` les UV du modèle :

```text
nb = normalize(2*T3(UV).rgb - 1)
nt = normalize(2*T4(UVtar).rgb - 1)
r1 = T2(UV    + (M2.y, 0)).r
r2 = T2(UVtar + (M2.y, 0)).r
ab = M1.x + M2.z*sin(6.2829999923706055*r1)
at = 0.5  + M2.z*sin(6.2829999923706055*r2)
bxy = nb.xy * ab
txy = nt.xy * at
B = normalize((bxy, sqrt(1.0000100135803223-dot(bxy,bxy))))
T = normalize((txy, sqrt(1-dot(txy,txy))))
N = normalize(B + w*T)
```

`w=1` pour le tar pur ; `w=T0(UV).a` pour les passes hybrides 2 et 5.
Dans ces deux passes, la couleur diffuse est aussi mélangée :

```text
A = mix(T0(UV).rgb, tarColor.rgb, w)
```

Pour les autres passes, `A=tarColor.rgb`. Il ne faut donc pas multiplier le
résultat par `COLOR.rgb` : le rouge des sommets est une donnée de progression,
pas une teinte d'albédo. Le RGB de la texture de base n'est pas utilisé comme
albédo dans les passes pures de progression 0 et 7.

## 4. Éclairage complet et reflets

Tous les vecteurs ci-dessous sont normalisés dans le repère tangent : `V`
va de la caméra vers la surface (`TEX2`), `L` est la lumière directionnelle
(`TEX4`) et `Hemi` l'axe hémisphérique transmis (`TEX3`).

```text
D = max(dot(N,L),0) * shadow
h = 0.5*dot(N,Hemi) + 0.5
Hemisphere = mix(colorGround.rgb, colorSky.rgb, h)
Lighting = Hemisphere + colorDirLight.rgb*D
Phong = max(dot(N, normalize(L-V)),0)^M0.x * D
```

Les variantes KbTarModel1..4 ajoutent respectivement 0, 1, 2 ou 3 lumières
ponctuelles. Pour chaque lumière i, `Pi=TEX(5+i).xyz` :

```text
distance_i = length(Pi)
Li = normalize(Pi)
attenuation_i = clamp((pLightParam_i.w - distance_i + pLightParam_i.y)
                      / pLightParam_i.w, 0, 1)
Di = max(dot(N,Li),0)*attenuation_i
Lighting += pLightColor_i.rgb*Di
Phong = max(Phong, max(dot(N,normalize(Li-V)),0)^M0.x * Di)
```

La diffusion est additionnée ; le spéculaire de Phong retient le **maximum**
des contributions, pas leur somme. Les composantes x et z des paramètres
ponctuels ne sont pas utilisées par cette formule d'atténuation.

Le terme de Fresnel et le spéculaire supplémentaire sont :

```text
f = 1 - abs(dot(N,V))
F = M0.y + (M0.z-M0.y)*f
Spec = T1(UV).r*Phong + M0.w*f^3*max(dot(V,L),0)^2
R = 2*N*dot(N,V) - V
```

Si `envmapColor.w < 0.01`, `Env=T5(R).rgb`. Sinon, le programme utilise :

```text
r = -R
UVsphere = (0.5,0.5) + (r.x,-r.y) / (2*length(r+(0,0,1)))
Env = T6(UVsphere).rgb
```

Le RGB avant brouillard est :

```text
C = A*Lighting + specularColor.rgb*Spec + w*T1(UV).r*F*Env
```

Le facteur `w` sur les reflets vaut l'alpha de base seulement pour les passes
hybrides 2/5 ; il vaut 1 pour les autres. Les couleurs `envmapColor.rgb` ne
teintent pas ce calcul : le w de ce paramètre choisit le mode de réflexion.

## 5. Brouillard et sortie

`d=length(TEX1.xyz)` est la distance à la caméra. Le programme calcule trois
candidats, les sélectionne par `fogSelect.xyz`, puis mélange les couleurs :

```text
k = 1.4426950216293335  # littéral compilé, proche de log2(e)
Flinear = (fogParam.y-d)*fogParam.x
Fexp    = exp2(-fogParam.z*d*k)
Fexp2   = exp2(-fogParam.z^2*d^2*k)
fog = clamp(dot((Flinear,Fexp,Fexp2),fogSelect.xyz),0,1)
RGBout = (fog*C + (1-fog)*fogColor.rgb) * halfLambertParam.x
```

L'ombre directionnelle est échantillonnée en coordonnées écran avec les
littéraux compilés pour 1280×720 et leurs décalages. L'interpréteur conserve
ces littéraux, même s'ils doivent être adaptés lors d'un port vers un autre
viewport. Les couleurs sont celles du shader avant conversion/blending ROP.

## 6. Les huit passes de chaque ressource

| Passe | Skinning | UV du tar | Sortie alpha |
| ---: | --- | --- | --- |
| 0 | 4 influences | procéduraux | masque rouge + tarHP + alpha test de base |
| 1 | 4 influences | procéduraux | tarColor.a, avec alpha test de base |
| 2 | 4 influences | procéduraux | tarColor.a ; mélange base/tar par alpha de base |
| 3 | 4 influences | UV modèle | tarColor.a, avec alpha test de base |
| 4 | 1 influence | procéduraux | tarColor.a, avec alpha test de base |
| 5 | 1 influence | procéduraux | tarColor.a ; mélange base/tar par alpha de base |
| 6 | 1 influence | UV modèle | tarColor.a, avec alpha test de base |
| 7 | 1 influence | procéduraux | masque rouge + tarHP + alpha test de base |

Ces indices sont ceux des descripteurs dans chaque ressource compilée. Les
identifier à un nom d'armure ou à un pourcentage de vie serait incorrect.

## 7. Vérification, reproduction et limites précises

```sh
python tools/extraction/pak_extractor.py game_files/package/PS3_GAME/USRDIR/finalizedPS3/KB/package/static.pak --out /tmp/majin-static --quiet
python tools/research/tar_shader_decode.py /tmp/majin-static/KB/shader output/research/tar_shader
python -m unittest tests.test_tar_shader tests.test_shader_inspect tests.test_rsx_fp_disasm -q
```

Le rapport généré comprend les SHA-256 des ressources et des programmes,
les 32 jeux de descripteurs, les deux stages, les constantes par nom et les
littéraux vertex. Les listes annotées sont régénérables sans inclure les
ressources originales dans Git. Les offsets `vertex_absolute_offset` et
`fragment_absolute_offset` du nouveau rapport sont absolus dans le fichier ;
l'ancien inspecteur expose un `fragment_program_offset` relatif à fxbf.

Les tests comparent **768 cas RGBA** (24 par passe : deux environnements,
trois modes de brouillard, positions et normales variables, quatre alphas
de base, transitions de masque et lumières ponctuelles) à un interpréteur distinct des instructions.
Ils vérifient aussi les UV de **128 cas vertex**, dans les quatre quadrants,
avec quatre matrices de skinning différentes et les branches originales.
Les seuils stricts, l'ordre abs/négation et les tables de relocations sont
couverts séparément. La tolérance de comparaison RGBA en arithmétique réelle
est de 1e-11. Ce n'est pas une comparaison pixel à pixel avec un émulateur ou
une capture GPU de la PS3.

Le décodeur complémentaire couvre les sept passes supplémentaires ignorées par
l'ancien inspecteur, et le parseur des constantes a été corrigé : il parcourait
les relocations jusqu'à un séparateur
au lieu de respecter leur nombre. Des tables jointives attribuaient alors un
patch à deux uniforms ; les zéros de fin de tarHP étaient aussi pris pour des
relocations. Les prédicats, écritures CC et cibles IFE manquants sont maintenant
exposés, ainsi que l'ordre correct `-abs(x)`.

Les équations des deux stages sont ainsi décodées pour ces ressources. Ce qui
reste à retrouver **en dehors du shader** : la fonction CPU PV→tarHP, les
valeurs animées complètes de uvOffset/matParam*, les sélections conditionnelles
des textures ambiantes, les états des samplers et du framebuffer. Les bindings
principaux et plusieurs constantes CPU sont maintenant établis dans le rapport
d’intégration lié en introduction. Les reproduire ne peut pas être
remplacé par une formule inventée à partir des seuls noms d'uniforms.

## Références d'encodage

Les dispositions de bits ont été recoupées avec les définitions publiques
[RSXFragmentProgram.h](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Emu/RSX/Program/RSXFragmentProgram.h),
[RSXVertexProgram.h](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Emu/RSX/Program/RSXVertexProgram.h),
[FragmentProgramDecompiler.cpp](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Emu/RSX/Program/FragmentProgramDecompiler.cpp)
et [VertexProgramDecompiler.cpp](https://github.com/RPCS3/rpcs3/blob/master/rpcs3/Emu/RSX/Program/VertexProgramDecompiler.cpp)
de RPCS3. Les noms, constantes, équations et programmes analysés proviennent
des ressources locales du jeu.
