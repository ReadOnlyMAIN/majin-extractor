#!/usr/bin/env python3
"""Disassemble the straight-line subset of RSX/NV40 fragment bytecode.

RSX fragment instructions are 128-bit records whose bytes are swapped inside
each 16-bit half-word. A source of type CONSTANT makes the following 128-bit
record inline literal data rather than another instruction.

This tool intentionally reports every encoded source, even when the opcode or
destination mask means that source is unused. It is aimed at short compiled Cg
programs such as ``KbCloudModel_1`` and does not reconstruct control flow.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.research.shader_inspect import extract_program_blobs  # noqa: E402


OPCODES = [
    "NOP", "MOV", "MUL", "ADD", "MAD", "DP3", "DP4", "DST",
    "MIN", "MAX", "SLT", "SGE", "SLE", "SGT", "SNE", "SEQ",
    "FRC", "FLR", "KIL", "PK4", "UP4", "DDX", "DDY", "TEX",
    "TXP", "TXD", "RCP", "RSQ", "EX2", "LG2", "LIT", "LRP",
    "STR", "SFL", "COS", "SIN", "PK2", "UP2", "POW", "PKB",
    "UPB", "PK16", "UP16", "BEM", "PKG", "UPG", "DP2A", "TXL",
    "UNKNOWN_30", "TXB", "UNKNOWN_32", "TEXBEM", "TXPBEM", "BEMLUM",
    "REFL", "TIMESWTEX", "DP2", "NRM", "DIV", "DIVSQ", "LIF",
    "FENCT", "FENCB", "UNKNOWN_3F", "BRK", "CAL", "IFE", "LOOP",
    "REP", "RET",
]
OPERANDS = {
    "NOP": 0, "MOV": 1, "MUL": 2, "ADD": 2, "MAD": 3,
    "DP3": 2, "DP4": 2, "MIN": 2, "MAX": 2, "TEX": 1,
    "DIVSQ": 2, "NRM": 1, "TXL": 2, "TXB": 2,
}
ATTRIBUTES = [
    "WPOS", "COL0", "COL1", "FOGC", "TEX0", "TEX1", "TEX2",
    "TEX3", "TEX4", "TEX5", "TEX6", "TEX7", "TEX8", "TEX9", "SSA",
]
CHANNELS = "xyzw"
SCALE_NAMES = {0: "1", 1: "2", 2: "4", 3: "8", 5: "1/2", 6: "1/4", 7: "1/8"}


def decode_record(record: bytes) -> tuple[int, int, int, int]:
    if len(record) != 16:
        raise ValueError("an RSX fragment record must contain 16 bytes")
    decoded = b"".join(record[index:index + 2][::-1] for index in range(0, 16, 2))
    return struct.unpack("<4I", decoded)


def _swizzle(word: int) -> str:
    value = "".join(CHANNELS[(word >> shift) & 3] for shift in (9, 11, 13, 15))
    if value == "xyzw":
        return ""
    if len(set(value)) == 1:
        value = value[0]
    return "." + value


def decode_source(word: int, source_index: int, attribute: int) -> dict:
    register_type = word & 3
    register_index = (word >> 2) & 0x3F
    fp16 = bool((word >> 8) & 1)
    absolute_bit = 29 if source_index == 0 else 18
    if register_type == 0:
        base = ("H" if fp16 else "R") + str(register_index)
        kind = "temporary"
    elif register_type == 1:
        base = ATTRIBUTES[attribute] if attribute < len(ATTRIBUTES) else f"ATTR{attribute}"
        kind = "input"
    elif register_type == 2:
        base = "CONST"
        kind = "constant"
    else:
        base = "UNKNOWN"
        kind = "unknown"
    text = base + _swizzle(word)
    if (word >> 17) & 1:
        text = "-" + text
    if (word >> absolute_bit) & 1:
        text = "|" + text + "|"
    return {
        "text": text,
        "kind": kind,
        "register_index": register_index,
        "fp16": fp16,
        "swizzle": _swizzle(word).lstrip("."),
        "negate": bool((word >> 17) & 1),
        "absolute": bool((word >> absolute_bit) & 1),
    }


def disassemble(payload: bytes) -> list[dict]:
    if len(payload) % 16:
        raise ValueError("fragment program size is not a multiple of 16")
    instructions = []
    slot = 0
    while slot * 16 < len(payload):
        words = decode_record(payload[slot * 16:(slot + 1) * 16])
        destination, src0, src1, src2 = words
        opcode_id = ((destination >> 24) & 0x3F) | (((src1 >> 31) & 1) << 6)
        opcode = OPCODES[opcode_id] if opcode_id < len(OPCODES) else f"OP_{opcode_id:02X}"
        attribute = (destination >> 13) & 0xF
        sources = [
            decode_source(src0, 0, attribute),
            decode_source(src1, 1, attribute),
            decode_source(src2, 2, attribute),
        ]
        mask = "".join(
            channel for bit, channel in zip(range(9, 13), CHANNELS)
            if (destination >> bit) & 1
        )
        no_destination = bool((destination >> 30) & 1)
        target = None if no_destination else (
            ("H" if (destination >> 7) & 1 else "R")
            + str((destination >> 1) & 0x3F)
            + (("." + mask) if mask and mask != "xyzw" else "")
        )
        scale = (src1 >> 28) & 7
        item = {
            "slot": slot,
            "byte_offset": slot * 16,
            "opcode": opcode,
            "opcode_id": opcode_id,
            "destination": target,
            "write_mask": mask,
            "texture_unit": (destination >> 17) & 0xF,
            "input_attribute": ATTRIBUTES[attribute]
            if attribute < len(ATTRIBUTES) else f"ATTR{attribute}",
            "sources": sources,
            "operand_count": OPERANDS.get(opcode, 3),
            "result_scale": SCALE_NAMES.get(scale, f"unknown({scale})"),
            "saturate": bool((destination >> 31) & 1),
            "end": bool(destination & 1),
            "raw_words": [f"0x{word:08x}" for word in words],
        }
        if any(source["kind"] == "constant" for source in sources):
            literal_slot = slot + 1
            if literal_slot * 16 >= len(payload):
                raise ValueError(f"instruction in slot {slot} has a truncated literal")
            literal_words = decode_record(
                payload[literal_slot * 16:(literal_slot + 1) * 16]
            )
            item["literal_slot"] = literal_slot
            item["literal"] = [
                struct.unpack("<f", struct.pack("<I", word))[0]
                for word in literal_words
            ]
            slot += 1
        instructions.append(item)
        slot += 1
        if item["end"]:
            break
    return instructions


def format_assembly(instructions: list[dict]) -> str:
    lines = []
    for instruction in instructions:
        used = instruction["sources"][:instruction["operand_count"]]
        operands = [source["text"] for source in used]
        if instruction["opcode"] in {"TEX", "TXL", "TXB"}:
            operands.append(f'texture[{instruction["texture_unit"]}]')
        prefix = instruction["destination"] + ", " if instruction["destination"] else ""
        suffix = ""
        if instruction["result_scale"] != "1":
            suffix += f' ; scale={instruction["result_scale"]}'
        if "literal" in instruction:
            suffix += f' ; literal={instruction["literal"]}'
        lines.append(
            f'{instruction["slot"]:02d}: {instruction["opcode"]:<6} '
            + prefix + ", ".join(operands) + suffix
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="0bxf shader resource or raw .fp blob")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    data = args.input.read_bytes()
    programs = extract_program_blobs(data) if data[:4] == b"0bxf" else [(args.input.stem, data)]
    report = [
        {"name": name, "size": len(payload), "instructions": disassemble(payload)}
        for name, payload in programs
    ]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for program in report:
            print(f'[{program["name"]}] {program["size"]} bytes')
            print(format_assembly(program["instructions"]))


if __name__ == "__main__":
    main()
