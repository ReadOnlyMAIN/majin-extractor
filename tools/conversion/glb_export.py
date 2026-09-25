"""Self-contained glTF 2.0 export of decoded DDM geometry (no Blender required).

Map objects are reconstructed connected components, not recovered authoring
instances. Positions are already in world space. Node translations recenter
editable objects without applying the DDM bounding-box rotations as transforms.
"""
from collections import defaultdict
import hashlib
import json
import math
import struct


def split_objects(vertices, mesh_parts, mode):
    """Join indexed faces and exact shared edges across UV/material seams.

    Spatially close surfaces and duplicated vertices touching at only one point
    are not welded. Original vertex attributes are never modified or merged.
    """
    faces = [(part, triangle) for part in mesh_parts for triangle in part['triangles']]
    if not faces:
        raise ValueError('Cannot export a GLB without triangles.')
    if mode == 'single':
        return [faces]
    if mode == 'submeshes':
        groups = defaultdict(list)
        for part, triangle in faces:
            groups[part['submesh_index']].append((part, triangle))
        return list(groups.values())
    if mode != 'connected':
        raise ValueError(f'Unknown object separation mode: {mode}')
    parents = list(range(len(faces)))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(a, b):
        a, b = root(a), root(b)
        if a != b:
            parents[max(a, b)] = min(a, b)

    vertex_faces, edge_faces = {}, {}
    for i, (_, triangle) in enumerate(faces):
        for index in triangle:
            if index in vertex_faces:
                join(i, vertex_faces[index])
            else:
                vertex_faces[index] = i
        points = [tuple(vertices[index]['position']) for index in triangle]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if points[a] == points[b]:
                continue
            edge = tuple(sorted((points[a], points[b])))
            if edge in edge_faces:
                join(i, edge_faces[edge])
            else:
                edge_faces[edge] = i
    groups = defaultdict(list)
    for i, face in enumerate(faces):
        groups[root(i)].append(face)
    return list(groups.values())


def remove_redundant_surface_passes(vertices, mesh_parts, materials):
    """Remove exact duplicate faces used only as an extra normal-map pass.

    The map renderer can draw the same surface more than once to combine
    textures. Core glTF materials cannot represent that legacy multipass
    operation, and exporting both surfaces causes z-fighting. A normal-only
    face is discarded only when an exactly coincident diffuse face exists.
    Exact duplicates within one material are also collapsed.
    """
    roles = {
        material['index']: {texture.get('role') for texture in material.get('textures', [])}
        for material in materials
    }
    faces_by_geometry = defaultdict(list)
    for part in mesh_parts:
        for triangle in part['triangles']:
            key = tuple(sorted(tuple(vertices[index]['position']) for index in triangle))
            faces_by_geometry[key].append((part, triangle))

    def oriented_face_signature(triangle):
        values = []
        for index in triangle:
            vertex = vertices[index]
            values.append((
                tuple(vertex['position']), tuple(vertex.get('normal', ())),
                tuple(vertex.get('tangent', ())), tuple(vertex.get('bitangent', ())),
                tuple(vertex.get('uv', ())), vertex.get('color'),
            ))
        rotations = [tuple(values[offset:] + values[:offset]) for offset in range(3)]
        return min(rotations)

    kept_by_part = defaultdict(list)
    removed_normal_passes = 0
    removed_exact_duplicates = 0
    for faces in faces_by_geometry.values():
        diffuse_materials = {
            part['material_index'] for part, _ in faces
            if 'diffuse' in roles.get(part['material_index'], set())
        }
        seen_faces = set()
        for part, triangle in faces:
            material = part['material_index']
            material_roles = roles.get(material, set())
            normal_only = 'normal' in material_roles and 'diffuse' not in material_roles
            if diffuse_materials and normal_only:
                removed_normal_passes += 1
                continue
            signature = (material, oriented_face_signature(triangle))
            if signature in seen_faces:
                removed_exact_duplicates += 1
                continue
            seen_faces.add(signature)
            kept_by_part[id(part)].append(triangle)

    filtered = []
    for part in mesh_parts:
        triangles = kept_by_part.get(id(part), [])
        if triangles:
            filtered.append(part | {'triangles': triangles})
    return filtered, {
        'removed_normal_only_surface_passes': removed_normal_passes,
        'removed_exact_duplicate_faces': removed_exact_duplicates,
    }


def normalized(vector):
    length = math.sqrt(sum(x * x for x in vector))
    return tuple(x / length for x in vector) if length > 1e-20 else (0.0, 1.0, 0.0)


def tangent_frame(vertex, normal):
    """Orthogonalize packed DDM tangents and preserve bitangent handedness."""
    tangent = vertex.get('tangent', (1.0, 0.0, 0.0))
    dot = sum(n * t for n, t in zip(normal, tangent))
    tangent = tuple(t - dot * n for n, t in zip(normal, tangent))
    if sum(t * t for t in tangent) < 1e-20:
        axis = min(range(3), key=lambda i: abs(normal[i]))
        tangent = tuple(float(i == axis) - normal[axis] * normal[i] for i in range(3))
    tangent = normalized(tangent)
    n, t = normal, tangent
    cross = (n[1]*t[2] - n[2]*t[1], n[2]*t[0] - n[0]*t[2], n[0]*t[1] - n[1]*t[0])
    bitangent = vertex.get('bitangent', cross)
    sign = -1.0 if sum(a * b for a, b in zip(cross, bitangent)) < 0 else 1.0
    return (*tangent, sign)


def write_glb(path, vertices, mesh_parts, materials, object_name, scale,
              mode='connected', image_data=None, roughness=0.8):
    """Write nodes with local origins, material primitives, and embedded PNGs.

    Exact identical local meshes share a mesh index, including materials and
    every exported vertex attribute. No approximate instance matching is used.
    """
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('GLB scale must be finite and positive.')
    if roughness is not None and (not math.isfinite(roughness) or not 0 <= roughness <= 1):
        raise ValueError('GLB roughness must be between zero and one.')
    image_data = image_data or {}
    doc = {
        'asset': {'version': '2.0', 'generator': 'majin-extractor'},
        'scene': 0, 'scenes': [{'name': object_name, 'nodes': []}],
        'nodes': [], 'meshes': [], 'materials': [],
        'buffers': [], 'bufferViews': [], 'accessors': [],
    }
    binary = bytearray()

    def view(payload, target=None):
        binary.extend(b'\0' * (-len(binary) % 4))
        item = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(payload)}
        if target is not None:
            item['target'] = target
        binary.extend(payload)
        doc['bufferViews'].append(item)
        return len(doc['bufferViews']) - 1

    def accessor(payload, count, kind, component=5126, bounds=None):
        item = {'bufferView': view(payload, 34963 if kind == 'SCALAR' else 34962),
                'componentType': component, 'count': count, 'type': kind}
        if bounds is not None:
            item.update(min=bounds[0], max=bounds[1])
        doc['accessors'].append(item)
        return len(doc['accessors']) - 1

    texture_cache = {}

    def texture_index(output):
        if output not in texture_cache:
            payload = image_data.get(output)
            if payload is None:
                payload = (path.parent / output).read_bytes()
            if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'Expected a PNG texture: {output}')
            images = doc.setdefault('images', [])
            textures = doc.setdefault('textures', [])
            images.append({'bufferView': view(payload), 'mimeType': 'image/png',
                           'name': output})
            textures.append({'source': len(images) - 1})
            texture_cache[output] = len(textures) - 1
        return texture_cache[output]

    material_indices = {}
    for material in materials:
        phong = material.get('phong') or {}
        diffuse = [min(1.0, max(0.0, x)) for x in phong.get('diffuse', [1, 1, 1])]
        derived_roughness = (material.get('pbr_estimate') or {}).get('roughness', 1.0)
        exported_roughness = derived_roughness if roughness is None else roughness
        pbr = {'baseColorFactor': diffuse + [1.0], 'metallicFactor': 0.0,
               'roughnessFactor': exported_roughness}
        item = {'name': material.get('name', f"material_{material['index']}"),
                'pbrMetallicRoughness': pbr,
                'extras': {'ddm_material_index': material['index'],
                           'legacy_phong': phong, 'metallic_is_export_default': True}}
        item['extras']['roughness'] = {
            'exported': exported_roughness,
            'derived_from_phong_shininess': derived_roughness,
            'source': 'phong_shininess' if roughness is None else 'export_override',
        }
        for texture in material.get('textures', []):
            output = texture.get('output')
            role = texture.get('role')
            if not output or role not in ('diffuse', 'normal'):
                continue
            info = {'index': texture_index(output)}
            if role == 'diffuse':
                pbr['baseColorTexture'] = info
            else:
                item['normalTexture'] = info
        material_indices[material['index']] = len(doc['materials'])
        doc['materials'].append(item)

    mesh_cache = {}
    mesh_parts, filtering = remove_redundant_surface_passes(
        vertices, mesh_parts, materials,
    )
    groups = split_objects(vertices, mesh_parts, mode)
    for number, faces in enumerate(groups):
        used = sorted({index for _, triangle in faces for index in triangle})
        points = [vertices[i]['position'] for i in used]
        center = tuple((min(p[a] for p in points) + max(p[a] for p in points)) / 2
                       for a in range(3))
        translation = [x * scale for x in center]
        by_material = defaultdict(list)
        for part, triangle in faces:
            by_material[part['material_index']].append(triangle)
        packed = []
        signature = hashlib.sha256()
        for material, triangles in sorted(by_material.items()):
            used_indices = sorted({index for triangle in triangles for index in triangle})
            remap = {index: local for local, index in enumerate(used_indices)}
            positions, normals, uvs, colors, tangents = [], [], [], [], []
            for index in used_indices:
                vertex = vertices[index]
                positions.append(tuple((x - c) * scale for x, c in zip(vertex['position'], center)))
                normals.append(normalized(vertex['normal']))
                tangents.append(tangent_frame(vertex, normals[-1]))
                # glTF and decoded PNG both use a top-left texture origin.
                uvs.append(vertex['uv'])
                color = vertex.get('color', 0xFFFFFFFF)
                colors.append(tuple(((color >> shift) & 255) / 255 for shift in (24, 16, 8, 0)))
            streams = []
            for values, width in ((positions, 3), (normals, 3), (uvs, 2), (colors, 4), (tangents, 4)):
                if not all(math.isfinite(x) for value in values for x in value):
                    raise ValueError('Non-finite vertex attribute in GLB geometry.')
                streams.append(b''.join(struct.pack(f'<{width}f', *value) for value in values))
            local_indices = [remap[index] for triangle in triangles for index in triangle]
            index_bytes = struct.pack(f'<{len(local_indices)}I', *local_indices)
            # Bounds must describe the actual float32 accessor values.
            float_positions = list(struct.iter_unpack('<3f', streams[0]))
            bounds = [[fn(p[a] for p in float_positions) for a in range(3)] for fn in (min, max)]
            signature.update(struct.pack('<3I', material, len(used_indices), len(local_indices)))
            for stream in (*streams, index_bytes):
                signature.update(stream)
            packed.append((material, len(used_indices), len(local_indices), streams, index_bytes, bounds))
        key = signature.digest()
        name = f'{object_name}_object_{number:04d}'
        if key not in mesh_cache:
            primitives = []
            for material, count, index_count, streams, index_bytes, bounds in packed:
                attrs = {label: accessor(stream, count, kind, bounds=bounds if label == 'POSITION' else None)
                         for label, stream, kind in zip(
                             ('POSITION', 'NORMAL', 'TEXCOORD_0', 'COLOR_0', 'TANGENT'), streams,
                             ('VEC3', 'VEC3', 'VEC2', 'VEC4', 'VEC4'))}
                primitives.append({'attributes': attrs,
                                   'indices': accessor(index_bytes, index_count, 'SCALAR', 5125),
                                   'material': material_indices[material], 'mode': 4})
            mesh_cache[key] = len(doc['meshes'])
            doc['meshes'].append({'name': name, 'primitives': primitives})
        node = {'name': name, 'mesh': mesh_cache[key], 'translation': translation,
                'extras': {'separation': mode, 'origin': 'reconstructed_aabb_center',
                           'ddm_submeshes': sorted({p['submesh_index'] for p, _ in faces})}}
        doc['scenes'][0]['nodes'].append(len(doc['nodes']))
        doc['nodes'].append(node)
    doc['buffers'] = [{'byteLength': len(binary)}]
    json_bytes = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
    json_bytes += b' ' * (-len(json_bytes) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    with path.open('wb') as stream:
        stream.write(struct.pack('<4sII', b'glTF', 2, total))
        stream.write(struct.pack('<I4s', len(json_bytes), b'JSON'))
        stream.write(json_bytes)
        stream.write(struct.pack('<I4s', len(binary), b'BIN\0'))
        stream.write(binary)
    return {'object_count': len(groups), 'mesh_count': len(doc['meshes']),
            'triangle_count': sum(len(p['triangles']) for p in mesh_parts),
            'separation': mode, 'embedded_image_count': len(doc.get('images', [])),
            **filtering}


def _joint_global_matrices(joints, scale):
    """Build row-major global bind matrices from glTF-order XYZW quaternions."""
    globals_ = [None] * len(joints)

    def matrix_for(index):
        if globals_[index] is not None:
            return globals_[index]
        joint = joints[index]
        x, y, z, w = joint['rotation']
        tx, ty, tz = (value * scale for value in joint['translation'])
        local = [
            1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), tx,
            2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), ty,
            2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), tz,
            0.0, 0.0, 0.0, 1.0,
        ]
        parent = joint['parent']
        if parent is None:
            result = local
        else:
            a, b = matrix_for(parent), local
            result = [
                sum(a[row*4+k] * b[k*4+column] for k in range(4))
                for row in range(4) for column in range(4)
            ]
        globals_[index] = result
        return result

    return [matrix_for(index) for index in range(len(joints))]


def _inverse_rigid_matrix_column_major(matrix):
    rotation_t = [[matrix[column*4+row] for column in range(3)] for row in range(3)]
    translation = matrix[3], matrix[7], matrix[11]
    inverse_translation = [
        -sum(rotation_t[row][column] * translation[column] for column in range(3))
        for row in range(3)
    ]
    row_major = [
        rotation_t[0][0], rotation_t[0][1], rotation_t[0][2], inverse_translation[0],
        rotation_t[1][0], rotation_t[1][1], rotation_t[1][2], inverse_translation[1],
        rotation_t[2][0], rotation_t[2][1], rotation_t[2][2], inverse_translation[2],
        0.0, 0.0, 0.0, 1.0,
    ]
    return [row_major[row*4+column] for column in range(4) for row in range(4)]


def write_skinned_glb(path, vertices, mesh_parts, materials, skeleton,
                      object_name, scale, image_data=None, roughness=0.8):
    """Write one skinned glTF mesh with joints, weights and bind matrices."""
    if not vertices or not mesh_parts or not skeleton.get('joints'):
        raise ValueError('Skinned GLB requires vertices, triangles, and joints.')
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('GLB scale must be finite and positive.')
    if roughness is not None and (not math.isfinite(roughness) or not 0 <= roughness <= 1):
        raise ValueError('GLB roughness must be between zero and one.')
    image_data = image_data or {}
    doc = {
        'asset': {'version': '2.0', 'generator': 'majin-extractor'},
        'scene': 0, 'scenes': [{'name': object_name, 'nodes': []}],
        'nodes': [], 'meshes': [], 'materials': [], 'skins': [],
        'buffers': [], 'bufferViews': [], 'accessors': [],
    }
    binary = bytearray()

    def view(payload, target=None):
        binary.extend(b'\0' * (-len(binary) % 4))
        item = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(payload)}
        if target is not None:
            item['target'] = target
        binary.extend(payload)
        doc['bufferViews'].append(item)
        return len(doc['bufferViews']) - 1

    def accessor(payload, count, kind, component=5126, bounds=None, target=None):
        item = {'bufferView': view(payload, target), 'componentType': component,
                'count': count, 'type': kind}
        if bounds is not None:
            item.update(min=bounds[0], max=bounds[1])
        doc['accessors'].append(item)
        return len(doc['accessors']) - 1

    texture_cache = {}

    def texture_index(output):
        if output not in texture_cache:
            payload = image_data.get(output)
            if payload is None:
                payload = (path.parent / output).read_bytes()
            if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'Expected a PNG texture: {output}')
            images = doc.setdefault('images', [])
            textures = doc.setdefault('textures', [])
            images.append({'bufferView': view(payload), 'mimeType': 'image/png',
                           'name': output})
            textures.append({'source': len(images) - 1})
            texture_cache[output] = len(textures) - 1
        return texture_cache[output]

    material_indices = {}
    materials_by_index = {material['index']: material for material in materials}
    for material in materials:
        phong = material.get('phong') or {}
        diffuse = [min(1.0, max(0.0, x)) for x in phong.get('diffuse', [1, 1, 1])]
        derived = (material.get('pbr_estimate') or {}).get('roughness', 1.0)
        exported = derived if roughness is None else roughness
        pbr = {'baseColorFactor': diffuse + [1.0], 'metallicFactor': 0.0,
               'roughnessFactor': exported}
        item = {
            'name': material.get('name', f"material_{material['index']}"),
            'pbrMetallicRoughness': pbr,
            'extras': {
                'ddm_material_index': material['index'],
                'legacy_phong': phong,
                'roughness': {'exported': exported,
                              'derived_from_phong_shininess': derived,
                              'source': 'phong_shininess' if roughness is None else 'export_override'},
            },
        }
        for texture in material.get('textures', []):
            output, role = texture.get('output'), texture.get('role')
            if not output or role not in ('diffuse', 'normal'):
                continue
            info = {'index': texture_index(output)}
            if role == 'diffuse':
                pbr['baseColorTexture'] = info
            else:
                item['normalTexture'] = info
        material_indices[material['index']] = len(doc['materials'])
        doc['materials'].append(item)

    joints = skeleton['joints']
    for joint in joints:
        doc['nodes'].append({
            'name': joint['name'],
            'translation': [value * scale for value in joint['translation']],
            'rotation': list(joint['rotation']),
            'extras': {'ddm_global_bone_id': joint['global_id']},
        })
    roots = []
    for index, joint in enumerate(joints):
        parent = joint['parent']
        if parent is None:
            roots.append(index)
        else:
            doc['nodes'][parent].setdefault('children', []).append(index)
    global_matrices = _joint_global_matrices(joints, scale)
    inverse_bind = b''.join(
        struct.pack('<16f', *_inverse_rigid_matrix_column_major(matrix))
        for matrix in global_matrices
    )
    inverse_accessor = accessor(
        inverse_bind, len(joints), 'MAT4', target=None,
    )
    doc['skins'].append({
        'name': f'{object_name}_skeleton',
        'inverseBindMatrices': inverse_accessor,
        'joints': list(range(len(joints))),
        'skeleton': roots[0] if len(roots) == 1 else roots[0],
    })

    primitives = []
    surfaces = defaultdict(list)
    for part in mesh_parts:
        signature = tuple(
            tuple(
                (
                    tuple(vertices[index]['position']),
                    tuple(vertices[index]['normal']),
                    tuple(vertices[index]['tangent']),
                    tuple(vertices[index]['bitangent']),
                    tuple(vertices[index]['uv']),
                    tuple(vertices[index]['joints']),
                    tuple(vertices[index]['weights']),
                    vertices[index].get('color'),
                )
                for index in triangle
            )
            for triangle in part['triangles']
        )
        surfaces[signature].append(part)
    variant_indices = {}
    exported_triangle_count = 0
    for _surface_signature, surface_parts in surfaces.items():
        triangles = surface_parts[0]['triangles']
        surface_materials = [part['material_index'] for part in surface_parts]
        material = surface_materials[0]
        exported_triangle_count += len(triangles)
        used = sorted({index for triangle in triangles for index in triangle})
        remap = {index: local for local, index in enumerate(used)}
        positions, normals, tangents, uvs, colors, joint_values, weights = (
            [], [], [], [], [], [], []
        )
        for index in used:
            vertex = vertices[index]
            positions.append(tuple(value * scale for value in vertex['position']))
            normal = normalized(vertex['normal'])
            normals.append(normal)
            tangents.append(tangent_frame(vertex, normal))
            uvs.append(vertex['uv'])
            color = vertex.get('color', 0xFFFFFFFF)
            colors.append(tuple(((color >> shift) & 255) / 255 for shift in (24, 16, 8, 0)))
            joint_values.append(vertex['joints'])
            weights.append(vertex['weights'])
        if not all(math.isfinite(x) for values in (positions, normals, tangents, uvs, weights)
                   for value in values for x in value):
            raise ValueError('Non-finite skinned vertex attribute.')
        position_bytes = b''.join(struct.pack('<3f', *value) for value in positions)
        float_positions = list(struct.iter_unpack('<3f', position_bytes))
        bounds = [[fn(p[axis] for p in float_positions) for axis in range(3)]
                  for fn in (min, max)]
        attributes = {
            'POSITION': accessor(position_bytes, len(used), 'VEC3', bounds=bounds, target=34962),
            'NORMAL': accessor(b''.join(struct.pack('<3f', *v) for v in normals), len(used), 'VEC3', target=34962),
            'TANGENT': accessor(b''.join(struct.pack('<4f', *v) for v in tangents), len(used), 'VEC4', target=34962),
            'TEXCOORD_0': accessor(b''.join(struct.pack('<2f', *v) for v in uvs), len(used), 'VEC2', target=34962),
            'COLOR_0': accessor(b''.join(struct.pack('<4f', *v) for v in colors), len(used), 'VEC4', target=34962),
            'JOINTS_0': accessor(b''.join(struct.pack('<4H', *v) for v in joint_values), len(used), 'VEC4', 5123, target=34962),
            'WEIGHTS_0': accessor(b''.join(struct.pack('<4f', *v) for v in weights), len(used), 'VEC4', target=34962),
        }
        local_indices = [remap[index] for triangle in triangles for index in triangle]
        index_accessor = accessor(
            struct.pack(f'<{len(local_indices)}I', *local_indices),
            len(local_indices), 'SCALAR', 5125, target=34963,
        )
        primitive = {
            'attributes': attributes,
            'indices': index_accessor,
            'material': material_indices[material],
            'mode': 4,
        }
        if len(surface_materials) > 1:
            mappings = []
            variants = doc.setdefault('extensions', {}).setdefault(
                'KHR_materials_variants', {'variants': []},
            )['variants']
            for variant_material in surface_materials[1:]:
                if variant_material not in variant_indices:
                    variant_indices[variant_material] = len(variants)
                    variant = materials_by_index[variant_material]
                    variants.append({'name': variant.get(
                        'name', f'material_{variant_material}'
                    )})
                mappings.append({
                    'material': material_indices[variant_material],
                    'variants': [variant_indices[variant_material]],
                })
            primitive['extensions'] = {
                'KHR_materials_variants': {'mappings': mappings},
            }
            if 'KHR_materials_variants' not in doc.setdefault('extensionsUsed', []):
                doc['extensionsUsed'].append('KHR_materials_variants')
        primitives.append(primitive)
    doc['meshes'].append({'name': f'{object_name}_mesh', 'primitives': primitives})
    mesh_node = len(doc['nodes'])
    doc['nodes'].append({'name': object_name, 'mesh': 0, 'skin': 0})
    doc['scenes'][0]['nodes'] = roots + [mesh_node]
    doc['buffers'] = [{'byteLength': len(binary)}]
    json_bytes = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
    json_bytes += b' ' * (-len(json_bytes) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    with path.open('wb') as stream:
        stream.write(struct.pack('<4sII', b'glTF', 2, total))
        stream.write(struct.pack('<I4s', len(json_bytes), b'JSON'))
        stream.write(json_bytes)
        stream.write(struct.pack('<I4s', len(binary), b'BIN\0'))
        stream.write(binary)
    return {
        'object_count': 1,
        'mesh_count': 1,
        'triangle_count': exported_triangle_count,
        'source_triangle_count': sum(len(part['triangles']) for part in mesh_parts),
        'joint_count': len(joints),
        'embedded_image_count': len(doc.get('images', [])),
        'animation_count': 0,
        'material_variant_count': len(variant_indices),
    }
