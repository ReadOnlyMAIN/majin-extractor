# CURVE_BITSTREAM_FINDINGS (probe auto-generated)

The 4-byte curve header is `u16 descriptor_count, u16 sample_count` (BE). The body begins with exactly `descriptor_count` 3-bit codes, read least-significant-first within each BE 24-bit word. Transform meanings remain open.

| asset | bones | logical slots | unique blocks | empty/duplicate slots | descriptors min..max | samples min..max | sample−1 found in qstm durations |
|---|---:|---:|---:|---:|---:|---:|---:|
| gim103 | 11 | 8 | 8 | 0 | 36..44 | 2..93 | 4/8 |
| chr300 | 62 | 150 | 150 | 0 | 247..253 | 2..181 | 135/150 |
| chr370 | 58 | 38 | 38 | 0 | 227..231 | 20..146 | 3/38 |
| chr540 | 207 | 88 | 83 | 5 | 658..1112 | 34..433 | 54/83 |
| chr590 | 220 | 34 | 32 | 2 | 753..793 | 2..181 | 26/32 |

## gim103

- descriptor count modulo 4: 0:8
- descriptor code histogram: 0:34, 1:230, 2:1, 5:20, 6:13, 7:26
- exact blocks (`index: descriptors/samples: codes`):

  - `0: 40/93: 0011111111170011111115677771111111111111`
  - `1: 40/42: 0011111111170011111115555551111111111111`
  - `2: 40/40: 0011111111170011111116677671111111111111`
  - `3: 36/2: 001111111115001111111111111111111111`
  - `4: 36/2: 001111111115001111111111111111111111`
  - `5: 44/93: 00111111111700111111157777710677661111111111`
  - `6: 44/42: 00111111111700111111155555510255551111111111`
  - `7: 44/40: 00111111111700111111166676716677771111111111`

## chr300

- descriptor count modulo 4: 1:103, 3:47
- descriptor code histogram: 0:3322, 1:22887, 2:10, 3:157, 4:147, 5:3723, 6:423, 7:7107

## chr370

- descriptor count modulo 4: 1:6, 3:32
- descriptor code histogram: 0:455, 1:5524, 2:3, 5:79, 6:139, 7:2550

## chr540

- descriptor count modulo 4: 0:60, 2:23
- descriptor code histogram: 0:4465, 1:44266, 2:1, 4:344, 5:2585, 6:405, 7:13948

## chr590

- descriptor count modulo 4: 1:28, 3:4
- descriptor code histogram: 0:1638, 1:19542, 2:38, 3:19, 4:31, 5:1188, 6:216, 7:1572
