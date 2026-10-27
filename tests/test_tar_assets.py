import contextlib
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest

from tools.conversion.tar_assets import NAMES, configure_vector_texture_import, write_tar_globals
from tools.research.tar_cpu_inspect import ElfMemory


class TarAssetsTests(unittest.TestCase):
    def test_global_export_uses_exact_original_names_and_preserves_vector_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);textures=root/'KB/texture';textures.mkdir(parents=True)
            header=bytearray(0x88);header[:4]=b'\x00xet';struct.pack_into('>HH',header,0x80,4,4)
            payload=bytes(header)+struct.pack('<HHI',0xf800,0,0)
            for name in NAMES:(textures/name).write_bytes(payload)
            with contextlib.redirect_stdout(io.StringIO()):
                report=write_tar_globals(root/'KB',root/'decoded')
            self.assertEqual(len(report['textures']),3)
            for item in report['textures']:
                self.assertEqual(item['sha256'],hashlib.sha256(payload).hexdigest())
                target=root/'decoded/tar_globals'/item['png']
                self.assertTrue(target.is_file())
                settings=Path(str(target)+'.import').read_text()
                self.assertIn('compress/normal_map=2',settings)
                self.assertIn('compress/mode=0',settings)
                self.assertIn('mipmaps/generate=true',settings)

    def test_missing_globals_do_not_install_visual_substitutes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(FileNotFoundError):write_tar_globals(root/'KB',root/'out')
            self.assertFalse((root/'out').exists())

    def test_vector_import_patch_preserves_uid_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            image=Path(directory)/'normal.png';sidecar=Path(str(image)+'.import')
            sidecar.write_text('[remap]\nuid="uid://test"\n[params]\ncompress/mode=2\ncompress/normal_map=1\nprocess/size_limit=1024\n')
            configure_vector_texture_import(image);first=sidecar.read_text()
            configure_vector_texture_import(image)
            self.assertEqual(first,sidecar.read_text())
            self.assertIn('uid="uid://test"',first)
            self.assertIn('process/size_limit=1024',first)
            self.assertIn('compress/normal_map=2',first)


class CpuEvidenceTests(unittest.TestCase):
    def test_toc_load_respects_virtual_mapping_and_signed_offset(self):
        data=bytearray(512);data[:6]=b'\x7fELF\x02\x02'
        struct.pack_into('>Q',data,24,0x1000)
        struct.pack_into('>Q',data,32,64)
        struct.pack_into('>HH',data,54,56,1)
        struct.pack_into('>II6Q',data,64,1,5,256,0x1000,0x1000,256,256,4)
        struct.pack_into('>I',data,256,0xc002fffc)  # lfs f0,-4(r2)
        struct.pack_into('>f',data,272,0.006)
        memory=ElfMemory(bytes(data))
        self.assertAlmostEqual(memory.toc_float(0x1000,0x1014),0.006)
        with self.assertRaises(ValueError):memory.read(0x2000)
        struct.pack_into('>I',data,256,0)
        with self.assertRaises(ValueError):ElfMemory(bytes(data)).toc_float(0x1000,0x1014)

    def test_encrypted_self_is_not_accepted_as_an_elf(self):
        with self.assertRaises(ValueError):ElfMemory(b'SCE\x00'+bytes(100))
