"""Godot import settings that preserve baked animation constraints."""
from __future__ import annotations

import json
from pathlib import Path
import re


def _string_end(text, start):
    cursor = start + 1
    while cursor < len(text):
        if text[cursor] == '\\':
            cursor += 2
        elif text[cursor] == '"':
            return cursor + 1
        else:
            cursor += 1
    raise ValueError('unterminated Godot import string')


def _container_end(text, start):
    pairs = {'{': '}', '[': ']', '(': ')'}
    stack = []
    cursor = start
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            cursor = _string_end(text, cursor)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in '}])':
            if not stack or char != stack.pop():
                raise ValueError('unbalanced Godot import value')
            if not stack:
                return cursor + 1
        cursor += 1
    raise ValueError('truncated Godot import container')


def _dictionary_entries(text):
    """Locate string-keyed Variant values without evaluating their contents.

    Values may include Object(...), NodePath, StringName, arrays or resources;
    converting the entire ConfigFile to JSON would lose these custom settings.
    """
    cursor = 1
    entries = {}
    while cursor < len(text) - 1:
        while text[cursor].isspace() or text[cursor] == ',':
            cursor += 1
        if text[cursor] == '}':
            break
        if text[cursor] == '&':
            cursor += 1
        if text[cursor] != '"':
            raise ValueError('expected string key in Godot import dictionary')
        end = _string_end(text, cursor)
        key = json.loads(text[cursor:end])
        cursor = end
        while text[cursor].isspace():
            cursor += 1
        if text[cursor] != ':':
            raise ValueError('expected colon in Godot import dictionary')
        cursor += 1
        while text[cursor].isspace():
            cursor += 1
        start = cursor
        while cursor < len(text) and text[cursor] not in ',}':
            char = text[cursor]
            if char == '"':
                cursor = _string_end(text, cursor)
            elif char in '{[(':
                cursor = _container_end(text, cursor)
            else:
                cursor += 1
        end = cursor
        while end > start and text[end-1].isspace():
            end -= 1
        entries[key] = (start, end)
    return entries


def _merge_dictionary(text, path, updates):
    entries = _dictionary_entries(text)
    if path:
        key = path[0]
        if key in entries:
            start, end = entries[key]
            if text[start:start+1] != '{':
                raise ValueError(f'expected dictionary at {key}')
            value = _merge_dictionary(text[start:end], path[1:], updates)
            return text[:start] + value + text[end:]
        value = _merge_dictionary('{}', path[1:], updates)
        updates = {key: value}
    for key, value in updates.items():
        entries = _dictionary_entries(text)
        if key in entries:
            start, end = entries[key]
            text = text[:start] + value + text[end:]
        else:
            separator = ', ' if entries and not text[:-1].rstrip().endswith(',') else ' '
            text = text[:-1] + separator + json.dumps(key) + ': ' + value + '}'
    return text


def _update_parameters(body, frame_rate):
    for key, value in [('animation/fps', str(frame_rate)),
                       ('animation/remove_immutable_tracks', 'false')]:
        pattern = re.compile(r'^' + re.escape(key) + r'\s*=.*$', re.MULTILINE)
        if pattern.search(body):
            body = pattern.sub(f'{key}={value}', body)
        else:
            body += f'\n{key}={value}\n'
    match = re.search(r'^_subresources\s*=\s*', body, re.MULTILINE)
    if match:
        start = match.end()
        if body[start:start+1] != '{':
            raise ValueError('expected Godot import subresource dictionary')
        end = _container_end(body, start)
        raw = body[start:end]
    else:
        start = end = len(body)
        raw = '{}'
    updated = _merge_dictionary(raw, ['nodes', 'PATH:AnimationPlayer'], {
        'optimizer/enabled': 'false', 'compression/enabled': 'false'})
    if match:
        return body[:start] + updated + body[end:]
    return body + '\n_subresources=' + updated + '\n'


def _project_scene_preset(glb_path, project_path=None):
    if project_path is None:
        project_path = next((parent for parent in Path(glb_path).resolve().parents
                             if (parent / 'project.godot').is_file()), None)
    if project_path is None:
        return None
    project_file = Path(project_path) / 'project.godot'
    text = project_file.read_text(encoding='utf-8')
    section = re.search(r'^\[importer_defaults\][ \t]*\n(.*?)(?=^\[|\Z)',
                        text, re.MULTILINE | re.DOTALL)
    if section is None:
        return None
    match = re.search(r'^scene\s*=\s*', section[1], re.MULTILINE)
    if match is None:
        return None
    start = match.end()
    if section[1][start:start+1] != '{':
        raise ValueError(f'expected scene import preset dictionary in {project_file}')
    raw = section[1][start:_container_end(section[1], start)]
    entries = _dictionary_entries(raw)
    body = '\n' + '\n'.join(f'{key}={raw[a:b]}' for key, (a, b) in entries.items()) + '\n'
    return body


def _write_import_parameters(glb_path, update):
    destination = Path(str(glb_path) + '.import')
    existed = destination.exists()
    text = destination.read_text(encoding='utf-8') if existed else (
        '[remap]\nimporter="scene"\ntype="PackedScene"\n\n[params]\n')
    section = re.search(r'^\[params\][ \t]*\n(.*?)(?=^\[|\Z)', text, re.MULTILINE | re.DOTALL)
    if section is None:
        raise ValueError(f'missing params section in {destination}')
    result = text[:section.start(1)] + update(section[1]) + text[section.end(1):]
    if result != text:
        destination.write_text(result, encoding='utf-8')
    return {"file": destination.name, "created": not existed,
            "updated": existed and result != text, "preserved_existing": existed}


def apply_scene_import_preset(glb_path: Path, project_path=None) -> dict | None:
    """Apply the project's complete scene preset, retaining resource identity.

    Values are copied as Godot Variants, including UID references and native
    subresources. No animation overrides are added to an explicit user preset.
    """
    preset = _project_scene_preset(glb_path, project_path)
    if preset is None:
        return None
    result = _write_import_parameters(glb_path, lambda _body: preset)
    result.update(preset='importer_defaults/scene', preserved_existing=False)
    return result


def write_animation_import_settings(glb_path: Path, frame_rate: float = 30.0) -> dict:
    """Seed Godot's import config without simplifying baked joint constraints.

    An explicit project preset takes precedence over decoder recommendations.
    Without one, preserve existing customizations and disable simplification.
    """
    preset = apply_scene_import_preset(glb_path)
    if preset is not None:
        return preset
    return _write_import_parameters(glb_path, lambda body: _update_parameters(body, frame_rate))
