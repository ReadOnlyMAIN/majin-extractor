import base64
from collections import Counter
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools.conversion.glb_export import (
    remove_redundant_surface_passes,
    split_objects,
    tangent_frame,
    write_glb,
    write_skinned_glb,
)


def vertex(point, uv=(0.25, 0.75)):
    return {'position': point, 'normal': (0, 0, 1), 'uv': uv, 'color': 0xFFFFFFFF}


def part(index, triangles, material=0):
    return {'submesh_index': index, 'material_index': material, 'triangles': triangles}


def read_glb(path):
    data = path.read_bytes()
    magic, version, length = struct.unpack_from('<4sII', data)
    assert (magic, version, length) == (b'glTF', 2, len(data))
    count, kind = struct.unpack_from('<I4s', data, 12)
    assert kind == b'JSON' and count % 4 == 0
    doc = json.loads(data[20:20 + count])
    size, kind = struct.unpack_from('<I4s', data, 20 + count)
    assert kind == b'BIN\0' and size % 4 == 0
    blob = data[28 + count:]
    assert len(blob) == size
    for view in doc['bufferViews']:
        assert view['byteOffset'] % 4 == 0
        assert view['byteOffset'] + view['byteLength'] <= doc['buffers'][0]['byteLength']
    return doc, blob


def values(doc, blob, index):
    acc = doc['accessors'][index]
    view = doc['bufferViews'][acc['bufferView']]
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[acc['type']]
    component = {5123: 'H', 5125: 'I', 5126: 'f'}[acc['componentType']]
    fmt = '<' + str(width) + component
    stride = struct.calcsize(fmt)
    assert acc['count'] * stride == view['byteLength']
    rows = [struct.unpack_from(fmt, blob, view['byteOffset'] + i * stride) for i in range(acc['count'])]
    if 'min' in acc:
        assert acc['min'] == [min(row[a] for row in rows) for a in range(width)]
        assert acc['max'] == [max(row[a] for row in rows) for a in range(width)]
    return rows


class GlbExportTests(unittest.TestCase):
    def test_skinned_export_contains_bind_matrices_weights_and_material_variant(self):
        vertices = [vertex(p) for p in (
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
        )]
        for item in vertices:
            item.update(tangent=(1, 0, 0), bitangent=(0, 1, 0),
                        joints=(0, 1, 0, 0), weights=(0.75, 0.25, 0, 0))
        parts = [part(0, [(0, 1, 2)], 3), part(1, [(3, 4, 5)], 7)]
        materials = [
            {'index': 3, 'name': 'default'},
            {'index': 7, 'name': 'leader'},
        ]
        skeleton = {'joints': [
            {'name': 'root', 'global_id': 0, 'parent': None,
             'translation': (1, 2, 3), 'rotation': (0, 0, 0, 1)},
            {'name': 'child', 'global_id': 1, 'parent': 0,
             'translation': (0, 1, 0), 'rotation': (0, 0, 0, 1)},
        ]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'skin.glb'
            result = write_skinned_glb(
                path, vertices, parts, materials, skeleton, 'skin', 0.01,
            )
            doc, blob = read_glb(path)
        self.assertEqual(result['source_triangle_count'], 2)
        self.assertEqual(result['triangle_count'], 1)
        self.assertEqual(result['material_variant_count'], 1)
        self.assertEqual(len(doc['skins']), 1)
        self.assertEqual(doc['nodes'][0]['children'], [1])
        self.assertEqual(doc['nodes'][2]['skin'], 0)
        primitive = doc['meshes'][0]['primitives'][0]
        self.assertEqual(values(doc, blob, primitive['attributes']['JOINTS_0']),
                         [(0, 1, 0, 0)] * 3)
        for weights in values(doc, blob, primitive['attributes']['WEIGHTS_0']):
            self.assertAlmostEqual(sum(weights), 1.0)
        matrices = values(doc, blob, doc['skins'][0]['inverseBindMatrices'])
        self.assertEqual(len(matrices), 2)
        self.assertNotIn('target', doc['bufferViews'][
            doc['accessors'][doc['skins'][0]['inverseBindMatrices']]['bufferView']])
        self.assertEqual(
            doc['extensions']['KHR_materials_variants']['variants'][0]['name'],
            'leader',
        )

    def test_removes_coincident_normal_only_pass_and_same_material_duplicates(self):
        vertices = [vertex(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        triangle = (0, 1, 2)
        parts = [
            part(0, [triangle, triangle], 0),
            part(1, [triangle], 1),
        ]
        materials = [
            {'index': 0, 'textures': [{'role': 'diffuse'}]},
            {'index': 1, 'textures': [{'role': 'normal'}]},
        ]
        filtered, stats = remove_redundant_surface_passes(vertices, parts, materials)
        self.assertEqual([p['triangles'] for p in filtered], [[triangle]])
        self.assertEqual(stats, {
            'removed_normal_only_surface_passes': 1,
            'removed_exact_duplicate_faces': 1,
        })

    def test_keeps_normal_only_face_without_a_coincident_diffuse_surface(self):
        vertices = [vertex(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        parts = [part(0, [(0, 1, 2)], 0)]
        materials = [{'index': 0, 'textures': [{'role': 'normal'}]}]
        filtered, stats = remove_redundant_surface_passes(vertices, parts, materials)
        self.assertEqual(filtered[0]['triangles'], [(0, 1, 2)])
        self.assertEqual(stats['removed_normal_only_surface_passes'], 0)

    def test_tangent_handedness_and_orthogonalization(self):
        tangent = tangent_frame({'tangent': (2, 0, 0.1), 'bitangent': (0, -1, 0)}, (0, 0, 1))
        self.assertEqual(tangent, (1.0, 0.0, 0.0, -1.0))
        fallback = tangent_frame({'tangent': (0, 0, 0)}, (0, 1, 0))
        self.assertEqual(fallback, (1.0, 0.0, 0.0, 1.0))

    def test_seams_join_materials_but_point_contacts_do_not(self):
        vertices = [vertex(p) for p in (
            (0, 0, 0), (1, 0, 0), (0, 1, 0),
            (1, 0, 0), (1, 1, 0), (0, 1, 0),  # Duplicated seam vertices.
            (1, 1, 0), (2, 1, 0), (1, 2, 0),  # Only a point in common.
            (1.0001, 0, 0), (1.0001, 1, 0), (0.0001, 1, 0),
        )]
        parts = [part(0, [(0, 1, 2)]), part(1, [(3, 4, 5)], 1),
                 part(2, [(6, 7, 8)]), part(3, [(9, 10, 11)])]
        groups = split_objects(vertices, parts, 'connected')
        self.assertEqual([len(g) for g in groups], [2, 1, 1])
        self.assertEqual(len(split_objects(vertices, parts, 'single')), 1)
        self.assertEqual(len(split_objects(vertices, parts, 'submeshes')), 4)

    def test_shared_mesh_instances_preserve_world_geometry_and_uvs(self):
        points = [(0, 0, 0), (2, 0, 0), (0, 2, 0),
                  (10, 0, 0), (12, 0, 0), (10, 2, 0)]
        vertices = [vertex(p) for p in points]
        parts = [part(0, [(0, 1, 2), (3, 4, 5)])]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'scene.glb'
            result = write_glb(path, vertices, parts, [{'index': 0}], 'scene', 0.01)
            doc, blob = read_glb(path)
        self.assertEqual(result['object_count'], 2)
        self.assertEqual(result['mesh_count'], 1)
        self.assertEqual(doc['nodes'][0]['mesh'], doc['nodes'][1]['mesh'])
        world = []
        for node in doc['nodes']:
            primitive = doc['meshes'][node['mesh']]['primitives'][0]
            attrs = primitive['attributes']
            positions = values(doc, blob, attrs['POSITION'])
            uvs = values(doc, blob, attrs['TEXCOORD_0'])
            self.assertTrue(all(uv == (0.25, 0.75) for uv in uvs))
            for (index,) in values(doc, blob, primitive['indices']):
                self.assertLess(index, len(positions))
                world.append(tuple(round(x + t, 6) for x, t in zip(positions[index], node['translation'])))
        expected = [tuple(x * 0.01 for x in p) for p in points]
        self.assertEqual(Counter(world), Counter(expected))

    def test_material_primitives_and_embedded_png(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        vertices = [vertex(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0))]
        parts = [part(0, [(0, 1, 2)]), part(1, [(1, 3, 2)], 1)]
        materials = [{'index': i, 'textures': [{'role': 'diffuse', 'output': 'texture.png'}]} for i in range(2)]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'scene.glb'
            write_glb(path, vertices, parts, materials, 'scene', 1, image_data={'texture.png': png})
            doc, blob = read_glb(path)
        self.assertEqual(len(doc['nodes']), 1)
        self.assertEqual(len(doc['meshes'][0]['primitives']), 2)
        self.assertEqual([p['material'] for p in doc['meshes'][0]['primitives']], [0, 1])
        self.assertEqual(len(doc['images']), 1)
        view = doc['bufferViews'][doc['images'][0]['bufferView']]
        self.assertEqual(blob[view['byteOffset']:view['byteOffset'] + view['byteLength']], png)
        self.assertNotIn('uri', doc['buffers'][0])
        self.assertNotIn('uri', doc['images'][0])

    def test_roughness_override_and_phong_fallback(self):
        vertices = [vertex(p) for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        materials = [{'index': 0, 'pbr_estimate': {'roughness': 0.25}}]
        with tempfile.TemporaryDirectory() as root:
            override = Path(root) / 'override.glb'
            derived = Path(root) / 'derived.glb'
            write_glb(override, vertices, [part(0, [(0, 1, 2)])], materials,
                      'scene', 1, roughness=0.8)
            write_glb(derived, vertices, [part(0, [(0, 1, 2)])], materials,
                      'scene', 1, roughness=None)
            override_doc, _ = read_glb(override)
            derived_doc, _ = read_glb(derived)
        self.assertEqual(
            override_doc['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], 0.8)
        self.assertEqual(
            derived_doc['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], 0.25)


if __name__ == '__main__':
    unittest.main()
