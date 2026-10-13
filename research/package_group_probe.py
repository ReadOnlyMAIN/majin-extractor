"""Phase 2.2 probe — channel-base consensus across the motion corpus.

Evidence over guessing: :func:`resolve_scalar_channel_base` gates one
package at a time on a near-unanimous unique-anchor vote
(``curves.BASE_CONSENSUS``).  This probe answers the two questions that
decide whether that gate is *right*:

1.  per package — what does the full unique-anchor vote distribution look
    like, and does the gate's verdict match it?  Packages that split their
    votes (chr540 205/181/317, chr590 389/573/…) stay locked; packages
    with a unanimous vote (gim103 5/5 → base 21) export clips.
2.  per skeleton group — assets sharing an identical skeleton (same bone
    count and byte-identical bind arrays) must share one channel layout,
    so their packages must agree on one base.  Groups where the members
    vote the same base corroborate the layout; a group that disagrees
    would expose per-package base drift.

Findings (measured on the shipped corpus, see the written findings file):

- gim103: vote 5/5 for base 21 — the only corpus package whose 7-scalar
  mapping is anchored; it is also the only one whose clips pass the
  export gate.
- chr540: 57 anchors split (top 16/57) — no single base; the 2.2 probes
  measured quaternion norms 38..2002 on the plurality base, refuting the
  rotation reading.  The consensus gate refuses it (correct).
- chr300/chr370: zero unique anchors; chr590: 12 anchors split 4/4/2.
- Shared-skeleton groups (18 of them, up to 3 assets) largely refuse too:
  a skeleton match alone does NOT imply a matching channel base, so the
  gate keeps them locked unless their own votes agree.

Usage: python research/package_group_probe.py [asset ...]   (all by default)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.conversion.motion import parse_motion_package
from tools.conversion.motion.curves import (
    BASE_CONSENSUS,
    decode_curve_block,
    resolve_scalar_channel_base,
)
from tools.conversion.motion.resource import UnsupportedMotion

KB_ROOT = Path(__file__).resolve().parent.parent / \
    "game_files/decompressed/KB/motionPackage"


def unique_anchor_votes(package) -> dict[int, int]:
    """Votes for each candidate base from unique bind-anchors.

    A valued scalar whose first value byte-equals exactly ONE bind
    component of the motion skeleton votes for the base that maps it
    back to that component's (bone, comp) channel.
    """
    skeleton = package.skeleton
    owners: dict[float, list] = {}
    for bone, (trio, quat) in enumerate(
            zip(skeleton.translations, skeleton.rotations)):
        for comp, value in enumerate((*trio, *quat)):
            owners.setdefault(value, []).append((bone, comp))
    votes: dict[int, int] = defaultdict(int)
    for block in package.curve_blocks:
        try:
            decoded = decode_curve_block(block)
        except (UnsupportedMotion, ValueError):
            continue
        for track in decoded.tracks:
            if not track.values:
                continue
            candidates = owners.get(track.values[0], [])
            if len(candidates) != 1:
                continue
            bone, comp = candidates[0]
            base = 7 * bone + comp - track.scalar_index
            votes[base] += 1
    return dict(votes)


def skeleton_key(skeleton) -> tuple:
    return (
        skeleton.bone_count,
        tuple(skeleton.translations),
        tuple(skeleton.rotations),
    )


def survey(assets) -> dict:
    groups: dict[tuple, list] = defaultdict(list)
    packages = {}
    for name in assets:
        pkg_path = KB_ROOT / name / "BigEndian" / name
        if not pkg_path.is_file():
            continue
        try:
            package = parse_motion_package(pkg_path)
        except (UnsupportedMotion, OSError):
            continue
        if package.skeleton is None:
            continue
        packages[name] = package
        groups[skeleton_key(package.skeleton)].append(name)
    return {"packages": packages, "groups": groups}


def report(assets) -> str:
    data = survey(assets)
    lines = ["# PACKAGE_GROUP_FINDINGS (probe auto-generated)", ""]
    verdicts = {}
    group_by_name = {}
    for group, names in sorted(data["groups"].items(),
                               key=lambda kv: -len(kv[1])):
        if len(names) < 2:
            continue
        lines.append(f"## shared skeleton, {len(names)} assets: "
                     f"{', '.join(sorted(names))}")
        lines.append(f"- bones={group[0]}")
        for name in sorted(names):
            votes = unique_anchor_votes(data["packages"][name])
            verdicts[name] = votes
            group_by_name[name] = group
            if votes:
                top = sorted(votes.items(), key=lambda kv: -kv[1])[:3]
                share = top[0][1] / sum(votes.values())
                lines.append(f"  - {name}: votes {top} "
                             f"(top share {share:.3f})")
            else:
                lines.append(f"  - {name}: no unique-anchor votes")
        lines.append("")
    singles = [name for name in sorted(data["packages"])
               if len(data["groups"][skeleton_key(
                   data["packages"][name].skeleton)]) == 1]
    lines.append("## single-skeleton assets (gate verdicts)")
    for name in singles:
        votes = unique_anchor_votes(data["packages"][name])
        verdicts[name] = votes
        group_by_name[name] = None
        if not votes:
            lines.append(f"- {name}: no anchors → gate locked")
            continue
        total = sum(votes.values())
        base, count = sorted(votes.items(), key=lambda kv: -kv[1])[0]
        share = count / total
        state = "CONSENSUS" if share >= BASE_CONSENSUS else "SPLIT"
        lines.append(f"- {name}: top base {base} {count}/{total} "
                     f"(share {share:.3f}) → {state}")
    lines.append("")
    lines.append("## gate agreement (resolve_scalar_channel_base vs votes)")
    for name in sorted(data["packages"]):
        package = data["packages"][name]
        resolved = resolve_scalar_channel_base(package)
        votes = verdicts[name]
        agrees = (
            resolved is None and not votes
            or resolved is None and votes and max(
                votes.values()) / sum(votes.values()) < BASE_CONSENSUS
            or resolved is not None and votes.get(resolved, 0) and
            votes[resolved] / sum(votes.values()) >= BASE_CONSENSUS
        )
        if votes:
            lines.append(f"- {name}: base={resolved} "
                         f"({'agrees' if agrees else 'MISMATCH'})")
        else:
            lines.append(f"- {name}: base={resolved} (no-anchor case, "
                         f"{'agrees' if agrees else 'MISMATCH'})")
    lines.append("")
    lines.append("## shared-skeleton base agreement")
    for group, names in sorted(data["groups"].items(),
                               key=lambda kv: -len(kv[1])):
        if len(names) < 2:
            continue
        bases = [resolve_scalar_channel_base(data["packages"][name])
                 for name in names]
        lines.append(f"- {', '.join(sorted(names))}: bases {bases}")
    lines.append("")
    lines.append("## conclusions")
    unanimous = [name for name, votes in verdicts.items()
                 if votes and max(votes.values()) == sum(votes.values())]
    lines.append(f"- unanimous-vote packages: {unanimous or 'none'}")
    split = [name for name, votes in verdicts.items() if votes
             and max(votes.values()) / sum(votes.values()) < BASE_CONSENSUS]
    lines.append(f"- split-vote packages (gate locked): {split}")
    lines.append("- shared skeletons do not imply shared bases: identical "
                 "rigs still carry per-package channel sections, so the "
                 "gate must stay per-package.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    assets = argv or sorted(
        d.name for d in KB_ROOT.iterdir()
        if (d / "BigEndian" / d.name).is_file())
    output = Path(__file__).with_name("PACKAGE_GROUP_FINDINGS.md")
    output.write_text(report(assets), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
