#!/usr/bin/env python3
"""Binary probe for skinned-DDM geometry detection and vertex decoding.

The 8-attribute skinned group is found by searching for ``u32 == 8`` (the
attribute count), but that word appears many times inside real vertex data, so
the first match can be a false positive (observed on chr500). This probe scores
every candidate structurally (chained sections, weight sums, palette validity)
so the correct group is chosen, and prints the checks that distinguish a real
group from a coincidental signature.

Run::

    python research/skin_decode_probe.py chr300 chr500 chr560
    python research/skin_decode_probe.py --all
    python research/skin_decode_probe.py --dump chr500 --offset 0x75a46
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "game_files/decompressed/KB"
sys.path.insert(0, str(ROOT / "tools" / "conversion"))


def u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def candidate_headers(data):
    """Yield every (offset, fields) where the attribute count reads as 8."""
    signature = struct.pack(">I", 8)
    search = 8
    while True:
        attribute_offset = data.find(signature, search)
        if attribute_offset < 0:
            break
        search = attribute_offset + 1
        offset = attribute_offset - 8
        if offset < 0 or offset + 24 > len(data):
            continue
        sections, submeshes, attributes, vertices, palette, indices = (
            struct.unpack_from(">6I", data, offset)
        )
        yield offset, {
            "sections": sections,
            "submeshes": submeshes,
            "attributes": attributes,
            "vertices": vertices,
            "palette": palette,
            "indices": indices,
        }


def score_group(data, offset, fields, stride=28):
    """Score a candidate skinned group by structural consistency."""
    sections = fields["sections"]
    vertices = fields["vertices"]
    indices = fields["indices"]
    palette = fields["palette"]
    if not (1 <= sections <= 64 and 1 <= fields["submeshes"] <= 64
            and fields["attributes"] == 8 and 3 <= vertices <= 1_000_000
            and 1 <= palette <= 256 and 3 <= indices <= 10_000_000):
        return None
    section_min = 20 + indices * 2 + vertices * 16 + vertices * stride + palette * 4
    if offset + 24 + section_min > len(data):
        return None
    cursor = offset + 24  # group header is count + 5 section-0 fields
    cursor += indices * 2
    cursor += vertices * 16
    first_vertex = cursor
    if first_vertex + stride > len(data):
        return None
    raw_weights = tuple(data[first_vertex + 24:first_vertex + 28])
    weight_sum = sum(raw_weights)
    palette_indices = tuple(data[first_vertex + 20:first_vertex + 24])
    return {
        "offset": offset,
        "sections": sections,
        "vertices": vertices,
        "indices": indices,
        "palette": palette,
        "first_weight_sum": weight_sum,
        "weights_valid": weight_sum == 255,
        "palette_indices_in_range": all(i < palette for i in palette_indices),
    }


def validate_group_weights(data, offset, max_vertices=256):
    """Check the 255-sum weight invariant over many vertices of section 0.

    Returns (checked, valid) counts. A real skinned group has *every* vertex's
    four weight bytes summing to 255; a coincidental signature rarely does.
    """
    try:
        sections, submeshes, attributes, vertices, palette, indices = (
            struct.unpack_from(">6I", data, offset)
        )
    except struct.error:
        return 0, 0
    cursor = offset + 24
    cursor += indices * 2
    cursor += vertices * 16
    checked = valid = 0
    for local in range(min(vertices, max_vertices)):
        start = cursor + local * 28
        if start + 28 > len(data):
            break
        weights = data[start + 24:start + 28]
        checked += 1
        if sum(weights) == 255:
            valid += 1
    return checked, valid
def analyze(name, dump_offset=None):


    path = KB / "chara" / name / name
    data = path.read_bytes()
    scored = []
    for offset, fields in candidate_headers(data):
        result = score_group(data, offset, fields)
        if result:
            scored.append(result)
    print(f"=== {name}: size={len(data)} candidates_with_valid_bounds="
          f"{len(scored)}")
    for row in scored:
        checked, valid = validate_group_weights(data, row["offset"])
        ratio = valid / checked if checked else 0.0
        print(f"   @0x{row['offset']:06x} secs={row['sections']:3d} "
              f"verts={row['vertices']:6d} idx={row['indices']:6d} "
              f"pal={row['palette']:3d} wsum={row['first_weight_sum']:3d} "
              f"pal_in_range={row['palette_indices_in_range']} "
              f"weight_ratio={valid}/{checked}={ratio:.2f}")
    if dump_offset is not None:
        dump_group(data, dump_offset)


def dump_group(data, offset):
    sections, submeshes, attributes, vertices, palette, indices = (
        struct.unpack_from(">6I", data, offset)
    )
    print(f"--- dump @0x{offset:x}: secs={sections} subm={submeshes} "
          f"attrs={attributes} verts={vertices} pal={palette} idx={indices}")
    cursor = offset + 24
    for section_index in range(min(sections, 3)):
        descriptor_count, attrs, verts, pal, idx = (
            struct.unpack_from(">5I", data, cursor)
        )
        print(f"  section {section_index} @0x{cursor:x}: desc={descriptor_count} "
              f"attrs={attrs} verts={verts} pal={pal} idx={idx}")
        cursor += 20
        cursor += idx * 2
        cursor += verts * 16
        if cursor + 28 <= len(data):
            pos = struct.unpack_from(">3f", data, cursor)
            color = u32(data, cursor + 12)
            uv = struct.unpack_from(">2e", data, cursor + 16)
            pi = tuple(data[cursor + 20:cursor + 24])
            rw = tuple(data[cursor + 24:cursor + 28])
            print(f"    v0 pos={tuple(round(x, 2) for x in pos)} "
                  f"color=0x{color:08x} uv={tuple(round(x, 3) for x in uv)} "
                  f"pal_idx={pi} weights={rw} sum={sum(rw)}")
        cursor += verts * 28
        cursor += pal * 4


def group_chain_is_consistent(data, offset, max_sections=64):
    """Walk a candidate group's sections to the end of the file.

    A real skinned group is stored as a run of sections that fills the file to
    within a small aligned padding. The declared section count is an upper
    bound, not exact (chr500 declares 10 but chains 6 before the file ends), so
    we walk until the data runs out and report how far we got. A false
    signature (a stray u32==8 in vertex data) stops early or overshoots.

    Returns (sections_walked, ok, end_offset).
    """
    try:
        sections, submeshes, attributes, vertices, palette, indices = (
            struct.unpack_from(">6I", data, offset)
        )
    except struct.error:
        return 0, False, offset
    if not 1 <= sections <= max_sections:
        return 0, False, offset
    cursor = offset + 4
    walked = 0
    for _ in range(sections):
        if cursor + 20 > len(data):
            break
        descriptor_count, attrs, verts, pal, idx = (
            struct.unpack_from(">5I", data, cursor)
        )
        if attrs != 8 or not 1 <= verts <= 1_000_000 or not 1 <= pal <= 256:
            return walked, False, cursor
        cursor += 20
        cursor += idx * 2
        cursor += verts * 16
        cursor += verts * 28
        cursor += pal * 4
        if cursor > len(data):
            return walked, False, cursor
        for _ in range(descriptor_count):
            if cursor + 60 > len(data):
                return walked, False, cursor
            bone_count = struct.unpack_from(">I", data, cursor + 56)[0]
            if bone_count > 100_000:
                return walked, False, cursor
            cursor += 60 + bone_count * 4
            if cursor > len(data):
                return walked, False, cursor
        walked += 1
    return walked, True, cursor


def select_best_group(name):
    """Choose the real skinned group among all candidates for one character.

    A character file can contain several 8-attribute groups (a main mesh plus
    accessory objects). The real group is the one whose *entire* section chain
    walks cleanly and whose weights keep the 255-sum invariant; this rejects the
    false first signature that makes chr500 fail.
    """
    data = (KB / "chara" / name / name).read_bytes()
    best = None
    for offset, fields in candidate_headers(data):
        scored = score_group(data, offset, fields)
        if not scored:
            continue
        walked, consistent, end = group_chain_is_consistent(data, offset)
        if not consistent or walked < 1:
            continue
        # A real group fills the file to within a small aligned padding; a false
        # signature stops early or lands on a weird boundary. This is the
        # decisive check (chr500's false first signature stops ~1 MB early).
        slack = len(data) - end
        if not 0 <= slack <= 64:
            continue
        checked, valid = validate_group_weights(data, offset)
        if not checked or valid / checked < 0.999:
            continue
        if not scored["palette_indices_in_range"]:
            continue
        scored["weight_ratio"] = valid / checked
        scored["sections_walked"] = walked
        scored["group_end"] = end
        scored["slack"] = slack
        # The group head is the FIRST such candidate by file order: chr300's
        # internal section (0xc68a) also chains to the end, but it (and every
        # later section) is preceded by the real head, so taking the earliest
        # one selects 0x1ade. chr500's false signature fails the slack test, so
        # the earliest passing candidate is its real head 0x75a46.
        return scored
    return None


def validate_skin(name):
    """Decode one character with the real decoder and report skin sanity.

    Checks the interpretation of the 28-byte vertex record: finite positions in
    a plausible range, half-float UVs, 255-sum byte weights, and joint indices
    that resolve inside the skeleton. This is what proves the skin decode, not
    just that it does not raise.
    """
    sys.path.insert(0, str(ROOT / "tools" / "conversion"))
    from ddm.skinned import (decode_skinned_geometry, decode_skinned_skeleton,
                             find_skinned_geometry_header)
    data = (KB / "chara" / name / name).read_bytes()
    header = find_skinned_geometry_header(data)
    if header is None:
        print(f"{name}: no skinned group")
        return
    skeleton = decode_skinned_skeleton(data)
    geometry = decode_skinned_geometry(data, header, skeleton)
    vertices = geometry["vertices"]
    finite = 0
    uv_ok = 0
    weight_ok = 0
    joint_ok = 0
    max_abs = 0.0
    for vertex in vertices:
        position = vertex["position"]
        if all(abs(value) < 1e7 and value == value for value in position):
            finite += 1
            max_abs = max(max_abs, max(abs(value) for value in position))
        u, v = vertex["uv"]
        if abs(u) < 1000 and abs(v) < 1000:
            uv_ok += 1
        if abs(sum(vertex["weights"]) - 1.0) < 1e-3:
            weight_ok += 1
        joints = vertex.get("joints", ())
        if joints and all(0 <= joint < len(skeleton["joints"]) for joint in joints):
            joint_ok += 1
    total = len(vertices)
    print(f"{name}: verts={total} finite={finite} uv_ok={uv_ok} "
          f"weight_ok={weight_ok} joint_ok={joint_ok} "
          f"max_abs={max_abs:.1f} parts={len(geometry['mesh_parts'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--best", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--dump", metavar="NAME")
    parser.add_argument("--offset", metavar="HEX")
    args = parser.parse_args()

    if args.dump:
        data = (KB / "chara" / args.dump / args.dump).read_bytes()
        offset = int(args.offset, 16) if args.offset else 0
        dump_group(data, offset)
        return

    if args.best:
        names = args.names
        if args.all or not names:
            names = sorted(entry.name for entry in (KB / "chara").iterdir()
                           if (entry / entry.name).is_file())
        for name in names:
            best = select_best_group(name)
            if best:
                print(f"{name:8s} best@0x{best['offset']:06x} "
                      f"secs={best['sections']} verts={best['vertices']} "
                      f"idx={best['indices']} pal={best['palette']} "
                      f"ratio={best['weight_ratio']:.2f}")
            else:
                print(f"{name:8s} no valid skinned group")
        return

    if args.validate:
        names = args.names
        if args.all or not names:
            names = sorted(entry.name for entry in (KB / "chara").iterdir()
                           if (entry / entry.name).is_file())
        for name in names:
            try:
                validate_skin(name)
            except Exception as error:
                print(f"{name}: ERROR {error}")
        return

    names = args.names
    if args.all or not names:
        names = sorted(entry.name for entry in (KB / "chara").iterdir()
                       if (entry / entry.name).is_file())
    for name in names:
        analyze(name)


if __name__ == "__main__":
    main()
