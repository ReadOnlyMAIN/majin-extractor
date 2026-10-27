import struct
import unittest

from tools.research.player_model_usage import cinematic_costumes, mhcp_chunks


class Memory:
    def __init__(self):
        self.data = {}

    def put(self, address, payload):
        self.data.update((address + i, byte) for i, byte in enumerate(payload))

    def read(self, address, size=4):
        return bytes(self.data[address + i] for i in range(size))

    def word(self, address):
        return struct.unpack('>I', self.read(address))[0]


class PlayerModelUsageTests(unittest.TestCase):
    def test_allocation_offsets_are_relative_to_mhcp_container(self):
        data = bytearray(0xbc)
        data[0x80:0x84] = b'MHCP'
        struct.pack_into('>II', data, 0x88, 2, 0x20)
        struct.pack_into('>4I', data, 0xa0, 0x30, 4, 0x34, 8)
        data[0xb0:0xbc] = b'one\0two\0\0\0\0\0'
        self.assertEqual(mhcp_chunks(bytes(data), b'MHCP'), [b'one\0', b'two\0\0\0\0\0'])
        struct.pack_into('>I', data, 0xa0, 0x10)
        with self.assertRaisesRegex(ValueError, 'outside payload'):
            mhcp_chunks(bytes(data), b'MHCP')

    def test_rejects_wrong_signature_and_truncated_allocation_directory(self):
        with self.assertRaisesRegex(ValueError, 'signature'):
            mhcp_chunks(b'bad', b'MHCP')
        data = bytearray(0xa0)
        data[0x80:0x84] = b'MHCP'
        struct.pack_into('>II', data, 0x88, 100, 0x20)
        with self.assertRaisesRegex(ValueError, 'directory'):
            mhcp_chunks(bytes(data), b'MHCP')

    def test_switch_follows_destinations_and_signed_toc_offsets(self):
        memory = Memory()
        memory.put(0x15880, struct.pack('>I', 0x2b80000a))
        ids = [101, 111, 116, 121, 126, 131, 136, 141, 151, 156, 146]
        for index, model in enumerate(ids):
            pc = 0x15a00 + (10 - index) * 4
            memory.put(0x158a4 + index * 4, struct.pack('>i', pc - 0x158a4))
            displacement = -32 + index * 4
            memory.put(pc, struct.pack('>I', 0x80620000 | (displacement & 65535)))
            address = 0x50000 + index * 64
            memory.put(0x80000 + displacement, struct.pack('>I', address))
            memory.put(address, f'KB/chara/chr{model}/chr{model}\0'.encode())
        result = cinematic_costumes(memory, 0x80000)
        self.assertEqual([row['path'].split('/')[-1] for row in result], [f'chr{i}' for i in ids])
        self.assertEqual(result[8]['path'], 'KB/chara/chr151/chr151')
        self.assertEqual(result[10]['path'], 'KB/chara/chr146/chr146')
        memory.put(0x15880, struct.pack('>I', 0x2b80000b))
        with self.assertRaisesRegex(ValueError, '11 cases'):
            cinematic_costumes(memory, 0x80000)


if __name__ == '__main__':
    unittest.main()
