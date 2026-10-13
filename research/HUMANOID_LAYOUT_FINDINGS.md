# HUMANOID_LAYOUT_FINDINGS (probe auto-generated)

## gim103

- bones=11, decoded blocks 8/8.
- quaternion oracle: 0/24 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 4 chains, biggest 3 blocks, mean intra-chain fidelity 0.958 (top sizes [3, 2, 2, 1]).
- descriptor-count variants: {36: 2, 40: 3, 44: 3}
- (count-9) alignment: %3 == 0 for 2 blocks, %5 == 0 for 3 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 1 runs at [24]
- mode-7 tangent clock: 6 one-frame gaps, implied fps 26.73.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 2 (dc=40, sc=40)
    s0: mode 0, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 1, first value carry/implicit
    s3: mode 1, first value carry/implicit
    s4: mode 1, first value carry/implicit
    s5: mode 1, first value carry/implicit
    s6: mode 1, first value carry/implicit
    s7: mode 1, first value carry/implicit
    s8: mode 1, first value carry/implicit
- exhaustive 4-subset quaternion test (first blocks): 0 hits / 1645 subsets ending at norm 1 on first AND last key.
- bind-anchor offset segments in block 0: s[21..23]→offset 21

## chr300

- bones=62, decoded blocks 150/150.
- quaternion oracle: 0/1920 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 31 chains, biggest 37 blocks, mean intra-chain fidelity 0.879 (top sizes [37, 31, 17, 14, 6, 5]).
- descriptor-count variants: {247: 5, 249: 15, 251: 42, 253: 88}
- (count-9) alignment: %3 == 0 for 15 blocks, %5 == 0 for 15 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 14 runs at [9, 14, 19, 24, 32, 120, 125, 148]
- mode-7 tangent clock: 10305 one-frame gaps, implied fps 30.00.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 2 (dc=253, sc=27)
    s0: mode 0, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 6, first value 0.47769
    s3: mode 6, first value 112.14700  (root bind T = (0.0000, 114.5400, 0.0000))
    s4: mode 6, first value -9.95522
    s5: mode 6, first value 0.00000
    s6: mode 7, first value -0.00000
    s7: mode 6, first value 0.00000
    s8: mode 1, first value carry/implicit
- exhaustive 4-subset quaternion test (first blocks): 0 hits / 15780921 subsets ending at norm 1 on first AND last key.

## chr370

- bones=58, decoded blocks 38/38.
- quaternion oracle: 0/342 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 26 chains, biggest 3 blocks, mean intra-chain fidelity 0.958 (top sizes [3, 3, 3, 2, 2, 2]).
- descriptor-count variants: {227: 4, 229: 6, 231: 28}
- (count-9) alignment: %3 == 0 for 28 blocks, %5 == 0 for 6 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 18 runs at [9, 14, 19, 24, 29, 39, 44, 59]
- mode-7 tangent clock: 2068 one-frame gaps, implied fps 30.00.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 11 (dc=231, sc=63)
    s0: mode 0, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 7, first value 0.00000
    s3: mode 7, first value 141.27000  (root bind T = (0.0000, 115.2590, -7.2087))
    s4: mode 7, first value -7.20866
    s5: mode 7, first value 0.00000
    s6: mode 7, first value -0.00000
    s7: mode 7, first value 0.00000
    s8: mode 1, first value carry/implicit
- exhaustive 4-subset quaternion test (first blocks): 0 hits / 12955005 subsets ending at norm 1 on first AND last key.

## chr540

- bones=207, decoded blocks 83/83.
- quaternion oracle: 0/1111 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 31 chains, biggest 11 blocks, mean intra-chain fidelity 0.860 (top sizes [11, 9, 6, 6, 5, 5]).
- descriptor-count variants: {658: 1, 670: 2, 672: 15, 676: 4, 688: 1, 772: 1, 778: 1, 782: 1, 784: 2, 788: 12, 798: 1, 800: 1, 802: 1, 804: 2, 840: 1, 844: 1, 848: 2, 850: 1, 852: 2, 856: 2, 858: 2, 860: 1, 862: 3, 864: 6, 866: 6, 868: 6, 870: 2, 874: 1, 1058: 1, 1112: 1}
- (count-9) alignment: %3 == 0 for 31 blocks, %5 == 0 for 12 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 49 runs at [9, 14, 19, 24, 29, 34, 39, 49]
- mode-7 tangent clock: 115033 one-frame gaps, implied fps 30.00.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 72 (dc=848, sc=90)
    s0: mode 0, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 0, first value carry/implicit
    s3: mode 5, first value 1024.18005  (root bind T = (0.0000, 804.7390, 31.8160))
    s4: mode 0, first value carry/implicit
    s5: mode 5, first value 0.12588
    s6: mode 0, first value carry/implicit
    s7: mode 0, first value carry/implicit
    s8: mode 1, first value carry/implicit
- exhaustive 4-subset quaternion test (first blocks): 0 hits / 5956753 subsets ending at norm 1 on first AND last key.
- bind-anchor offset segments in block 0: s[429..451]→offset 181, s[479..500]→offset 181, s[602..630]→offset 205, s[644..672]→offset 205, s[1022..1092]→offset 317 — MULTI-SECTION: no single base

## chr590

- bones=220, decoded blocks 32/32.
- quaternion oracle: 0/463 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 5 chains, biggest 20 blocks, mean intra-chain fidelity 0.940 (top sizes [20, 7, 3, 1, 1]).
- descriptor-count variants: {753: 5, 755: 1, 757: 22, 759: 3, 793: 1}
- (count-9) alignment: %3 == 0 for 8 blocks, %5 == 0 for 3 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 12 runs at [12, 17, 22, 284, 294, 370, 485, 495]
- mode-7 tangent clock: 1433 one-frame gaps, implied fps 29.55.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 0 (dc=757, sc=80)
    s0: mode 0, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 7, first value 0.00449
    s3: mode 7, first value 104.66600  (root bind T = (0.0000, 105.4910, 4.5042))
    s4: mode 7, first value 0.00702
    s5: mode 1, first value carry/implicit
    s6: mode 1, first value carry/implicit
    s7: mode 6, first value 1.27532
    s8: mode 7, first value -0.16413
- exhaustive 4-subset quaternion test (first blocks): 0 hits / 36702149 subsets ending at norm 1 on first AND last key.

## chr100

- bones=146, decoded blocks 367/367.
- quaternion oracle: 0/5608 consecutive dated quads are unit quaternions at every key — quaternion components RULED OUT
- carry chains: 220 chains, biggest 9 blocks, mean intra-chain fidelity 0.873 (top sizes [9, 8, 6, 6, 5, 5]).
- descriptor-count variants: {507: 3, 557: 6, 559: 32, 561: 82, 563: 32, 565: 10, 567: 22, 569: 11, 571: 17, 573: 27, 575: 19, 577: 22, 579: 29, 581: 13, 583: 30, 585: 9, 587: 1, 589: 2}
- (count-9) alignment: %3 == 0 for 172 blocks, %5 == 0 for 74 blocks.
- stride-5 [D,D,D,f,f] runs in block 0: 31 runs at [9, 14, 19, 24, 33, 38, 44, 53]
- mode-7 tangent clock: 45856 one-frame gaps, implied fps 30.00.
- root-prefix readout (biggest chain start):
  - first block of the biggest chain: block index 181 (dc=561, sc=13)
    s0: mode 2, first value carry/implicit
    s1: mode 0, first value carry/implicit
    s2: mode 7, first value -51.21570
    s3: mode 7, first value 97.08140  (root bind T = (0.0000, 98.6003, 0.6344))
    s4: mode 6, first value -250.59100
    s5: mode 0, first value carry/implicit
    s6: mode 5, first value 1.31752
    s7: mode 0, first value carry/implicit
    s8: mode 1, first value carry/implicit
- exhaustive 4-subset quaternion test (first blocks): 3 hits / 78440796 subsets ending at norm 1 on first AND last key.
- bind-anchor offset segments in block 0: s[34..35]→offset 9, s[58..60]→offset 19, s[73..75]→offset 25 — MULTI-SECTION: no single base

## Corpus conclusions

- quaternion-component reading: ruled out everywhere — gim103 0/24, chr300 0/1920, chr370 0/342, chr540 0/1111, chr590 0/463, chr100 0/5608.
- mode-7 tangents are per-second derivatives; the implied clock is 30 fps on the large humanoid packages.
- the 7-scalar-per-bone single-base model only survives on gim103 (translation-only); character packages split their descriptor list into sections with distinct offsets and triplet-shaped channel runs.
- root prefix (scalars 0..8) observed on chr300/chr540: s2..s4 = model-space root translation (chr300 112.15 vs root bind 114.54; chr540 1024.18 const), s5..s7 = root orientation deltas starting at 0.

