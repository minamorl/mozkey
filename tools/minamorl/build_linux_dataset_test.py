#!/usr/bin/env python3
"""Small, synthetic data format and reuse-boundary tests; no dictionary builder."""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zipfile

import build_linux_dataset as data
import build_linux_metadata as build
import build_linux_compat_test as fixtures


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def tiny_dataset(extra=b''):
    body = bytearray(b'\xefMOZC\r\n')
    metadata = bytearray()
    for name in ('dict', 'conn', 'pos_matcher', 'user_pos_token', 'user_pos_string', 'version'):
        chunk = b'24.11.oss' if name == 'version' else b'fixture' + extra
        entry = b'\x0a' + varint(len(name)) + name.encode() + b'\x10' + varint(len(body)) + b'\x18' + varint(len(chunk))
        metadata += b'\x0a' + varint(len(entry)) + entry
        body += chunk
    body += metadata + len(metadata).to_bytes(8, 'big')
    body += hashlib.sha1(body).digest()
    body += (len(body) + 8).to_bytes(8, 'big')
    return bytes(body)


def tiny_pos():
    value = '名詞'.encode()
    return struct.pack('<III', 1, 12, len(value)) + value + b'\0'


def tiny_elf(blob, marker, machine, symbol_size=None, tail=b''):
    blob += bytes((-len(blob)) % 8)
    size = len(blob) if symbol_size is None else symbol_size
    blob += tail
    strings = b'\0' + marker.encode() + b'\0'
    symbols = bytes(24) + struct.pack('<IBBHQQ', 1, 1, 0, 1, 0x1000, size)
    a, b, c = 64, 64 + len(blob), 64 + len(blob) + len(strings)
    section_offset = c + len(symbols)
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + bytes(9),
                         3, machine, 1, 0, 0, section_offset, 0, 64, 0, 0, 64, 4, 0)
    sections = bytes(64)
    sections += struct.pack('<IIQQQQIIQQ', 0, 1, 2, 0x1000, a, len(blob), 0, 0, 8, 0)
    sections += struct.pack('<IIQQQQIIQQ', 0, 3, 0, 0, b, len(strings), 0, 0, 1, 0)
    sections += struct.pack('<IIQQQQIIQQ', 0, 2, 0, 0, c, len(symbols), 2, 0, 8, 24)
    return header + blob + strings + symbols + sections


class DataReuseTest(unittest.TestCase):
    fake_git = fixtures.CompatibilityTest.fake_git
    archive = fixtures.CompatibilityTest.archive

    def setUp(self):
        fixtures.CompatibilityTest.setUp(self)

    def test_footer_metadata_and_corruption(self):
        blob = tiny_dataset()
        parsed, entries, version = data.dataset(blob + bytes((-len(blob)) % 8))
        self.assertEqual(parsed, blob)
        self.assertEqual(version, '24.11.oss')
        self.assertIn('dict', entries)
        corrupted = bytearray(blob)
        corrupted[8] ^= 1
        with self.assertRaisesRegex(ValueError, 'footer length/SHA1'):
            data.dataset(corrupted)
        with self.assertRaises(ValueError):
            data.dataset(blob[:-1])

    def test_pos_and_native_elf_boundaries(self):
        pos = tiny_pos()
        self.assertEqual(data.string_array(pos + bytes(5)), (pos, ['名詞']))
        for machine in (62, 183):
            blob = tiny_elf(tiny_dataset(), 'kOssMozcDataSet_data', machine)
            self.assertEqual(data.dataset(data.elf_symbol(blob, 'kOssMozcDataSet_data', machine)[0])[0], tiny_dataset())
            with self.assertRaises(ValueError):
                data.elf_symbol(blob, 'kOssMozcDataSet_data', 183 if machine == 62 else 62)

    def bundle(self):
        root = self.work / 'existing-data'
        root.mkdir()
        blob, pos = tiny_dataset(), tiny_pos()
        _, entries, version = data.dataset(blob)
        receipt = {'source_sha': data.SOURCE_SHA, 'pc_zip_sha256': data.PC_ZIP_SHA256,
                   'entries': entries, 'dataset_version': version, 'pos_names': ['名詞']}
        (root / 'mozc.data').write_bytes(blob)
        (root / 'pos_list.data').write_bytes(pos)
        build.write_json(root / 'dataset.json', receipt)
        for name, path in (('DATA_SHA256', root / 'mozc.data'), ('POS_SHA256', root / 'pos_list.data'),
                           ('RECEIPT_SHA256', root / 'dataset.json')):
            mock.patch.object(data, name, build.sha256(path)).start()
        return root

    def test_snapshot_notice_and_hash_rejection(self):
        existing = self.bundle()
        output = self.work / 'pc-snapshot'
        build.pc_snapshot(self.source, output, existing)
        digest = build.sha256(output / 'dictionary.json')
        result = build.validate_snapshot(self.source, output, digest)
        self.assertEqual(result['profile'], 'pc-embedded')
        self.assertEqual(result['schema_version'], 4)
        self.assertFalse((output / 'daily_sources.json').exists())
        (output / 'mozc.data').write_bytes(b'regenerated or changed')
        with self.assertRaisesRegex(ValueError, 'reused data hash mismatch'):
            build.validate_snapshot(self.source, output, digest)

    def test_built_arm_dataset_hash_is_required(self):
        existing = self.bundle()
        archive = self.work / 'native.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('usr/lib/mozc/mozc_server', tiny_elf(tiny_dataset(), 'kOssMozcDataSet_data', 183))
            zf.writestr('usr/lib/mozc/mozc_tool', tiny_elf(tiny_pos(), 'kPosArray_data', 183))
        self.assertEqual(data.verify_package(archive, 183)['dataset_sha256'], data.DATA_SHA256)
        with mock.patch.object(data, 'DATA_SHA256', '0' * 64), self.assertRaises(ValueError):
            data.verify_package(archive, 183)

    def test_postbuild_rejects_impossible_symbol_sizes(self):
        self.bundle()
        for machine in (62, 183):
            for changed in ('server', 'tool'):
                for delta in (-1, 1, 8):
                    with self.subTest(machine=machine, changed=changed, delta=delta):
                        archive = self.work / 'bad-size.zip'
                        with zipfile.ZipFile(archive, 'w') as zf:
                            for name, payload, marker in (
                                    ('server', tiny_dataset(), 'kOssMozcDataSet_data'),
                                    ('tool', tiny_pos(), 'kPosArray_data')):
                                size = (len(payload) + 7) // 8 * 8
                                encoded = tiny_elf(payload, marker, machine,
                                                   size + delta if name == changed else size,
                                                   tail=bytes(16))
                                zf.writestr(f'usr/lib/mozc/mozc_{name}', encoded)
                        with self.assertRaises(ValueError):
                            data.verify_package(archive, machine)

    def test_dataset_padding_is_zero_and_minimal(self):
        raw = tiny_dataset()
        if len(raw) % 8 == 0:
            raw = tiny_dataset(b'x')
        padding = (-len(raw)) % 8
        self.assertGreater(padding, 0)
        for machine in (62, 183):
            for tail in (bytes(padding), bytes(padding - 1) + b'\x01'):
                encoded = tiny_elf(raw + tail, 'kOssMozcDataSet_data', machine)
                array, _ = data.elf_symbol(encoded, 'kOssMozcDataSet_data', machine)
                if any(tail):
                    with self.assertRaisesRegex(ValueError, 'padding must be zero'):
                        data.dataset(array)
                else:
                    self.assertEqual(data.dataset(array)[0], raw)
        with self.assertRaises(ValueError):
            data.dataset(raw + bytes(padding + 8))

    def test_pos_padding_is_zero_and_minimal(self):
        raw = tiny_pos()
        for machine in (62, 183):
            valid = tiny_elf(raw, 'kPosArray_data', machine)
            self.assertEqual(data.string_array(data.elf_symbol(valid, 'kPosArray_data', machine)[0])[0], raw)
            padding = (-len(raw)) % 8
            invalid = tiny_elf(raw + bytes(padding - 1) + b'\x01', 'kPosArray_data', machine)
            with self.assertRaisesRegex(ValueError, 'padding/default'):
                data.string_array(data.elf_symbol(invalid, 'kPosArray_data', machine)[0])
        with self.assertRaises(ValueError):
            data.string_array(raw + bytes((-len(raw)) % 8 + 8))

    def test_overlay_does_not_invoke_dataset_macro_or_change_source(self):
        existing = self.bundle()
        package = self.source / 'src/data_manager/oss'
        package.mkdir(parents=True)
        original = '\nmozc_dataset(\n    name = "mozc_dataset_for_oss",\n)\n\nmozc_cc_library(\n    name = "oss_data_manager",\n)\n'
        (package / 'BUILD.bazel').write_text(original)
        (package / 'oss_data_manager.cc').write_text('// unchanged EA2 fixture\n')
        encoder = self.source / 'src/build_tools/embed_file.py'
        encoder.parent.mkdir(parents=True)
        encoder.write_text('# encoder fixture\n')
        def encode(argv, check):
            Path(argv[-1]).write_text('// already serialized bytes\n')
        destination = self.work / 'overlay'
        with mock.patch.object(data.subprocess, 'run', side_effect=encode):
            record = data.overlay(self.source, existing, destination)
        generated = (destination / 'data_manager/oss/BUILD.bazel').read_text()
        self.assertNotIn('\nmozc_dataset(', generated)
        self.assertIn('mozc_dataset_for_oss@header', generated)
        self.assertEqual((package / 'BUILD.bazel').read_text(), original)
        self.assertEqual(build.sha256(destination / 'data_manager/oss/mozc.data'), data.DATA_SHA256)
        self.assertIn('overlay_build_sha256', record)

    def test_public_dictionary_step_never_generates(self):
        script = Path(build.__file__).with_name('build_linux.sh').read_text()
        dictionary = script.split('  dictionary)', 1)[1].split('  package)', 1)[0]
        self.assertNotIn('prepare_daily_dictionary', dictionary)
        self.assertNotIn('pwsh', dictionary)
        self.assertIn(data.PC_ZIP_URL, dictionary)

    def test_snapshot_restore_and_package_provenance(self):
        existing = self.bundle()
        snapshot = self.work / 'pc-snapshot'
        build.pc_snapshot(self.source, snapshot, existing)
        digest = build.sha256(snapshot / 'dictionary.json')
        native = self.archive()
        target = self.source / 'src/bazel-bin/unix/mozc.zip'
        target.parent.mkdir(parents=True)
        with zipfile.ZipFile(native) as src, zipfile.ZipFile(target, 'w') as dst:
            for info in src.infolist():
                payload = src.read(info)
                if info.filename == 'usr/lib/mozc/mozc_server':
                    payload = tiny_elf(tiny_dataset(), 'kOssMozcDataSet_data', 183)
                elif info.filename == 'usr/lib/mozc/mozc_tool':
                    payload = tiny_elf(tiny_pos(), 'kPosArray_data', 183)
                dst.writestr(info, payload)
        runner = self.work / 'runner'
        runner.mkdir()
        package = runner / 'dataset-overlay/data_manager/oss'
        def existing_overlay(source, directory, destination):
            package.mkdir(parents=True)
            for name in ('mozc.data', 'pos_list.data'):
                (package / name).write_bytes((directory / name).read_bytes())
            return {'files_sha256': {p.name: build.sha256(p) for p in package.iterdir()}}
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': str(runner), 'EXPECTED_DICTIONARY_SHA256': digest}), \
                mock.patch.object(data, 'overlay', side_effect=existing_overlay), \
                mock.patch.object(build.subprocess, 'check_output', return_value=b'gcc\tfixture\n'):
            build.restore(self.source, snapshot, digest)
            output = self.work / 'output'
            build.package(self.source, output, snapshot, 'aarch64')
        result = json.loads((output / 'metadata.json').read_text())
        self.assertEqual(result['source_sha'], data.SOURCE_SHA)
        self.assertEqual(result['dictionary']['profile'], 'pc-embedded')
        self.assertEqual(result['dictionary_reuse']['embedded']['dataset_sha256'], data.DATA_SHA256)
        self.assertIn('--strip=never', result['build_recipe']['command'])
        self.assertFalse((self.source / build.GENERATED).exists())
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            fingerprint, name = line.split('  ')
            self.assertEqual(build.sha256(output / name), fingerprint)


if __name__ == '__main__':
    unittest.main(verbosity=2)
