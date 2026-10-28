# Player equipment: visibility and source placeholders

## Hair surfaces

The DDM hair chains have global bone IDs 190 (root), 191–192 (forelock),
193–194 (ponytail / "palmier"). Their authored surfaces are separate from the
face and scalp. The exporter groups the existing surfaces by these chains,
including the corresponding tar surfaces, without adding or deleting triangles.
Material names alone are insufficient: `ura` materials also occur on the face.

Godot's automatic headwear coverage is a preview policy based on the accessory
geometry, not a decoded original CPU visibility switch:

| Accessory | Hidden hair |
| --- | --- |
| chr720, chr723, chr725 | Ponytail and forelock |
| chr721, chr722 | Ponytail |
| chr726 | Forelock |
| chr724 (crown), none, source test cubes | Neither |

The inspector and Animation Lab expose an override to show all hair or hide
either/both groups. Removing the headwear restores the automatic hair state.

## Accessory tar

Observed tar materials use shader keys `0x00a073xx`, with the `0x00200000`
feature bit set, versus ordinary player materials `0x0084xxxx`. The former
name-based list missed `T_cap`, `T_hat`, `Tkannmuri`, `chr725_T`,
`chr726T_kamen`, `T_gauntlet`, `T_kote`, `Tude`, `chr745_T`, and `chr746T_kote`.
These surfaces now use the same tar shader, per-instance `tar_hp`, original
vertex-red mask, cinematic visibility and global textures as the body.
Legacy `tar_met` remains recognized by name when its render-state block cannot
be decoded. No new tar surfaces are generated.

## Last equipment entries

The following gray images are present in the local original XET resources.
Their decoded RGBA pixels are uniformly `(128, 128, 128, 255)`:

| Models | Source diffuse texture | Source geometry |
| --- | --- | --- |
| chr145 / chr146 | chr145_c01 | Body mesh with a single textured material |
| chr150 / chr155 | chr130_c02 | Clothing; skin/hair atlas chr1130_c01 exists |
| chr151 / chr156 | chr131_c02 | Clothing; skin/hair atlas chr131_c01 exists |
| chr727–729 | test | 24-vertex test cubes |
| chr747–749 | test | Test accessory geometry |

The last three body pairs are referenced by the original costume tables, and
the test accessories also have entries in LinkItemData. This does not establish
that they were finished or obtainable. Their source textures are retained and
the Animation Lab labels these variants as source placeholders. No replacement
texture or material appearance is invented.

User context (2026-10-28): the last three costumes are expected to be DLC
placeholders while their dedicated DLC PAKs have not been extracted. Keep
these entries as placeholders and defer DLC extraction until the end. The
association with particular DLC archives has not yet been independently checked.
