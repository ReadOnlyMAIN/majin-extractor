"""Convert HexaEngine HSC instance tables to a Godot FoliageSceneData resource.

The observed ``*_ins`` files store a matrix of offsets relative to 0x80.  Row
zero names the columns and every remaining row describes one instance.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import struct


MAGIC = b"\0hsc"
PAYLOAD_OFFSET = 0x80
FLOAT_COLUMNS = {
    "#TransX", "TransY", "TransZ",
    "RotateX", "RotateY", "RotateZ",
    "ScaleX", "ScaleY", "ScaleZ",
    "InsMagnitude", "InsHeight", "InsSpeed", "cullByDistance",
}
INTEGER_COLUMNS = {"isPerVertex", "GroupID", "isCullByDistance"}
MODEL_COLUMN = "ModelName"
EXPECTED_COLUMNS = FLOAT_COLUMNS | INTEGER_COLUMNS | {MODEL_COLUMN}


@dataclass(frozen=True)
class HSCInstance:
    translation: tuple[float, float, float]
    rotation_degrees: tuple[float, float, float]
    scale: tuple[float, float, float]
    model_name: str
    cull_by_distance: bool
    cull_distance: float
    group_id: int


def _cstring(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("ascii")


def parse_hsc_instances(data: bytes) -> list[HSCInstance]:
    """Decode the typed rows of an HSC ``*_ins`` table."""
    if len(data) < PAYLOAD_OFFSET + 8 or data[:4] != MAGIC:
        raise ValueError("Not a supported HSC instance table (missing \\0hsc magic).")

    column_count, row_count = struct.unpack_from(">II", data, PAYLOAD_OFFSET)
    if column_count == 0 or row_count < 1:
        raise ValueError("HSC table has no columns or header row.")
    cell_count = column_count * row_count
    table_start = PAYLOAD_OFFSET + 8
    table_end = table_start + cell_count * 4
    if table_end > len(data):
        raise ValueError("Truncated HSC cell-offset table.")

    relative_offsets = struct.unpack_from(f">{cell_count}I", data, table_start)
    absolute_offsets = [PAYLOAD_OFFSET + value for value in relative_offsets]
    if any(offset < table_end or offset >= len(data) for offset in absolute_offsets):
        raise ValueError("HSC cell offset points outside the data region.")
    if any(a >= b for a, b in zip(absolute_offsets, absolute_offsets[1:])):
        raise ValueError("HSC cell offsets are not strictly increasing.")

    cell_ends = absolute_offsets[1:] + [len(data)]
    cells = [data[start:end] for start, end in zip(absolute_offsets, cell_ends)]
    columns = [_cstring(cell) for cell in cells[:column_count]]
    if len(set(columns)) != len(columns):
        raise ValueError("HSC table contains duplicate column names.")
    missing = EXPECTED_COLUMNS - set(columns)
    if missing:
        raise ValueError(f"HSC instance table is missing columns: {sorted(missing)}")

    def decode(column: str, raw: bytes):
        if column in FLOAT_COLUMNS:
            if len(raw) < 4:
                raise ValueError(f"Truncated float cell in {column}.")
            return struct.unpack_from(">f", raw)[0]
        if column in INTEGER_COLUMNS:
            if len(raw) < 4:
                raise ValueError(f"Truncated integer cell in {column}.")
            return struct.unpack_from(">I", raw)[0]
        if column == MODEL_COLUMN:
            return _cstring(raw)
        raise ValueError(f"Unsupported HSC column {column!r}.")

    instances = []
    for row_index in range(1, row_count):
        first = row_index * column_count
        values = {
            column: decode(column, cells[first + column_index])
            for column_index, column in enumerate(columns)
        }
        numeric = [
            values[column] for column in FLOAT_COLUMNS
            if column in values
        ]
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError(f"HSC row {row_index} contains a non-finite number.")
        model_name = values[MODEL_COLUMN]
        if not model_name or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in model_name):
            raise ValueError(f"HSC row {row_index} has an unsafe model name {model_name!r}.")
        instances.append(HSCInstance(
            translation=(values["#TransX"], values["TransY"], values["TransZ"]),
            rotation_degrees=(values["RotateX"], values["RotateY"], values["RotateZ"]),
            scale=(values["ScaleX"], values["ScaleY"], values["ScaleZ"]),
            model_name=model_name,
            cull_by_distance=bool(values["isCullByDistance"]),
            cull_distance=values["cullByDistance"],
            group_id=values["GroupID"],
        ))
    return instances


def read_hsc_instances(path: Path) -> list[HSCInstance]:
    return parse_hsc_instances(path.read_bytes())


def _matrix_multiply(a, b):
    return tuple(tuple(
        sum(a[row][k] * b[k][column] for k in range(3))
        for column in range(3)
    ) for row in range(3))


def _rotation_matrix(rotation_degrees, order="XYZ"):
    x, y, z = (math.radians(value) for value in rotation_degrees)
    matrices = {
        "X": ((1.0, 0.0, 0.0),
              (0.0, math.cos(x), -math.sin(x)),
              (0.0, math.sin(x), math.cos(x))),
        "Y": ((math.cos(y), 0.0, math.sin(y)),
              (0.0, 1.0, 0.0),
              (-math.sin(y), 0.0, math.cos(y))),
        "Z": ((math.cos(z), -math.sin(z), 0.0),
              (math.sin(z), math.cos(z), 0.0),
              (0.0, 0.0, 1.0)),
    }
    result = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    # Intrinsic rotations: the first named axis is applied first.
    for axis in order:
        result = _matrix_multiply(matrices[axis], result)
    return result


def godot_transform(instance: HSCInstance, position_scale=0.01,
                    euler_order="XYZ", reflect_z=False):
    """Return the 12 values used by Godot's Transform3D text constructor."""
    rotation = _rotation_matrix(instance.rotation_degrees, euler_order)
    # HSC rotations use the source engine's row-vector convention. Godot's
    # basis axes are matrix columns, so transpose the composed source rotation
    # while copying it. This is easy to miss for quarter turns: +90 and -90
    # describe the same unoriented line, whereas intermediate headings expose
    # an error close to 90 degrees.
    basis = tuple(tuple(
        rotation[column][row] * instance.scale[column]
        for column in range(3)
    ) for row in range(3))
    translation = tuple(value * position_scale for value in instance.translation)

    if reflect_z:
        # Optional compatibility conversion for a pipeline that explicitly
        # mirrors imported geometry through Z. The regular DDM -> Godot path
        # preserves source coordinates for both the map and its instances.
        signs = (1.0, 1.0, -1.0)
        basis = tuple(tuple(
            signs[row] * basis[row][column] * signs[column]
            for column in range(3)
        ) for row in range(3))
        translation = tuple(signs[i] * translation[i] for i in range(3))

    # Transform3D serializes the three Basis columns, then the origin.
    return tuple(
        basis[row][column]
        for column in range(3)
        for row in range(3)
    ) + translation


def _number(value: float) -> str:
    if abs(value) < 5e-12:
        value = 0.0
    return format(value, ".9g")


def render_foliage_resource(
    instances: list[HSCInstance],
    *,
    foliage_data_script="res://addons/procedural_tools/foliage/resources/foliage_data.gd",
    foliage_scene_data_script="res://addons/procedural_tools/foliage/resources/foliage_scene_data.gd",
    mesh_path_template="res://terrain/foliage/meshes/{model}.res",
    position_scale=0.01,
    euler_order="XYZ",
    reflect_z=False,
) -> str:
    models = sorted({instance.model_name for instance in instances})
    mesh_ids = {model: f"mesh_{model}" for model in models}
    load_steps = 1 + 2 + len(models) + len(instances)
    lines = [f'[gd_resource type="Resource" load_steps={load_steps} format=3]', ""]
    lines.extend([
        f'[ext_resource type="Script" path="{foliage_data_script}" id="1_foliage_data"]',
        f'[ext_resource type="Script" path="{foliage_scene_data_script}" id="2_foliage_scene_data"]',
    ])
    for model in models:
        mesh_path = mesh_path_template.format(model=model)
        lines.append(
            f'[ext_resource type="ArrayMesh" path="{mesh_path}" id="{mesh_ids[model]}"]'
        )
    lines.append("")

    resource_ids = []
    for index, instance in enumerate(instances):
        resource_id = f"FoliageData_{index:04d}"
        resource_ids.append(resource_id)
        transform = ", ".join(_number(value) for value in godot_transform(
            instance, position_scale, euler_order, reflect_z,
        ))
        visibility_end = (
            instance.cull_distance * position_scale
            if instance.cull_by_distance else 0.0
        )
        lines.extend([
            f'[sub_resource type="Resource" id="{resource_id}"]',
            f'resource_name = "map_instance_{index:04d}_{instance.model_name}"',
            'script = ExtResource("1_foliage_data")',
            f"transform = Transform3D({transform})",
            f'mesh = ExtResource("{mesh_ids[instance.model_name]}")',
            f"visibility_ranges = Vector2(0, {_number(visibility_end)})",
            "",
        ])

    references = ", ".join(
        f'SubResource("{resource_id}")' for resource_id in resource_ids
    )
    lines.extend([
        "[resource]",
        'script = ExtResource("2_foliage_scene_data")',
        f'scene_folliage = Array[ExtResource("1_foliage_data")]([{references}])',
        "",
    ])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert a HexaEngine *_ins HSC table to FoliageSceneData.tres"
    )
    parser.add_argument("input", type=Path, help="Input HSC file, such as map101_ins")
    parser.add_argument("output", type=Path, help="Output Godot .tres file")
    parser.add_argument("--position-scale", type=float, default=0.01)
    parser.add_argument("--euler-order", choices=("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"), default="XYZ")
    parser.add_argument(
        "--mesh-path-template",
        default="res://terrain/foliage/meshes/{model}.res",
        help="Godot mesh path containing a {model} placeholder.",
    )
    parser.add_argument(
        "--foliage-data-script",
        default="res://addons/procedural_tools/foliage/resources/foliage_data.gd",
    )
    parser.add_argument(
        "--foliage-scene-data-script",
        default="res://addons/procedural_tools/foliage/resources/foliage_scene_data.gd",
    )
    parser.add_argument(
        "--reflect-z", action="store_true",
        help="Explicitly mirror transforms through Z for a mirrored mesh pipeline.",
    )
    parser.add_argument(
        "--require-existing-output-parent", action="store_true",
        help="Fail instead of creating the output's parent directory.",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.position_scale) or args.position_scale <= 0.0:
        parser.error("--position-scale must be finite and greater than zero")
    if "{model}" not in args.mesh_path_template:
        parser.error("--mesh-path-template must contain {model}")

    instances = read_hsc_instances(args.input)
    resource = render_foliage_resource(
        instances,
        foliage_data_script=args.foliage_data_script,
        foliage_scene_data_script=args.foliage_scene_data_script,
        mesh_path_template=args.mesh_path_template,
        position_scale=args.position_scale,
        euler_order=args.euler_order,
        reflect_z=args.reflect_z,
    )
    if args.require_existing_output_parent and not args.output.parent.is_dir():
        parser.error(f"output directory does not exist: {args.output.parent}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(resource, encoding="utf-8", newline="\n")
    counts = {model: sum(i.model_name == model for i in instances)
              for model in sorted({i.model_name for i in instances})}
    print(f"Wrote {len(instances)} instances to {args.output}")
    print("Models: " + ", ".join(f"{model}={count}" for model, count in counts.items()))


if __name__ == "__main__":
    main()
