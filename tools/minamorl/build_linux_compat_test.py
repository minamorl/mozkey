#!/usr/bin/env python3
"""Offline, Git-free fixtures. No Bazel, guest, CI dispatch or repository changes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import build_linux_compat as compat
import build_linux_metadata as build

PRIMARY = Path(__file__).with_name('build_linux_ea2e9f5c_sources')
RECIPE_SHA = '7' * 40


class CompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.source = self.work / 'source'
        shutil.copytree(PRIMARY, self.source)
        self.recipe = self.work / 'recipe'
        self.recipe.mkdir()
        for name, path in build.NOTICES.items():
            target = self.recipe / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'recipe supplemental fixture: {name}\n')
        lock = self.recipe / 'tools/dictionary/locks/daily_sources.json'
        lock.parent.mkdir(parents=True)
        lock.write_text('{"fixture":"locked inputs"}\n')
        self.trees = {root: {p.relative_to(root).as_posix(): p.read_bytes()
                             for p in root.rglob('*') if p.is_file()}
                      for root in (self.source, self.recipe)}
        generated = self.recipe / build.GENERATED
        generated.mkdir(parents=True)
        for name in build.DICTIONARIES:
            (generated / name).write_text(f'locked dictionary fixture: {name}\n')
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(build, 'git', side_effect=self.fake_git).start()
        mock.patch.object(build, 'trusted_recipe', return_value=(self.recipe, RECIPE_SHA)).start()
        mock.patch.dict(os.environ, {'EXPECTED_SOURCE_SHA': compat.SOURCE_SHA,
                                   'GITHUB_SHA': RECIPE_SHA, 'GITHUB_RUN_ID': 'fixture',
                                   'GITHUB_RUN_ATTEMPT': '1'}).start()

    def fake_git(self, root, *args):
        """In-memory committed trees; executing Git is forbidden in this run."""
        root = Path(root)
        revision = compat.SOURCE_SHA if root == self.source else RECIPE_SHA
        tree = self.trees[root]
        if args == ('rev-parse', 'HEAD'):
            return (revision + '\n').encode()
        if args[0] == 'show':
            ref, path = args[1].split(':', 1)
            self.assertIn(ref, (revision, 'HEAD'))
            return tree[path]
        if args[0] == 'ls-tree':
            filters = args[args.index('--') + 1:] if '--' in args else ()
            return b''.join(b'100644 blob ' + hashlib.sha1(data).hexdigest().encode() + b'\t'
                            + name.encode() + b'\0' for name, data in sorted(tree.items())
                            if not filters or any(name == p or name.startswith(p + '/') for p in filters))
        raise AssertionError(f'unsupported fixture Git operation: {args}')

    def archive(self, arch='aarch64'):
        work = self.work / f'package-{arch}'
        work.mkdir()
        reverse = {'mozc.png': 'product_icon_32bpp-128.png', 'mozc.svg': 'icon.svg',
                   'alpha_full.svg': 'full_ascii.svg', 'alpha_half.svg': 'half_ascii.svg',
                   'katakana_full.svg': 'full_katakana.svg', 'katakana_half.svg': 'half_katakana.svg'}
        icons = []
        for name in compat.BASE_ICONS:
            path = work / reverse.get(name, name)
            path.write_bytes(b'<svg><path d="M0 0"/></svg>' if name.endswith('.svg') else b'PNG fixture')
            icons.append(str(path))
        icons_zip = work / 'icons.zip'
        subprocess.run([sys.executable, str(PRIMARY / 'src/unix/build_icons.py'),
                        '--inputs', *icons, '--output', str(icons_zip)], check=True)
        inputs = [str(icons_zip)]
        for path in (*compat.BINARIES, compat.EMACS):
            name = 'ibus_mozc' if path == compat.BINARIES[-1] else Path(path).name
            target = work / name
            header = bytearray(64)
            header[:7] = b'\x7fELF\x02\x01\x01'
            header[16:18] = (3).to_bytes(2, 'little')
            header[18:20] = {'x86_64': 62, 'aarch64': 183}[arch].to_bytes(2, 'little')
            header[20:24] = (1).to_bytes(4, 'little')
            header[52:54] = (64).to_bytes(2, 'little')
            target.write_bytes(header)
            target.chmod(0o755)
            inputs.append(str(target))
        xml = work / 'mozc.xml'
        with xml.open('wb') as output:
            subprocess.run([sys.executable, str(PRIMARY / 'src/unix/ibus/gen_mozc_xml.py'),
                            '--branding=Mozc', '--ibus_mozc_path=/usr/lib/ibus-mozc/ibus-engine-mozc',
                            '--ibus_mozc_icon_path=/usr/share/ibus-mozc/product_icon.png',
                            '--server_dir=/usr/lib/mozc'], stdout=output, check=True)
        inputs.append(str(xml))
        client = work / 'mozc.el'
        client.write_text("(provide 'mozc)\n")
        inputs.append(str(client))
        archive = work / 'mozc.zip'
        subprocess.run([sys.executable, str(PRIMARY / 'src/unix/build_package.py'),
                        '--inputs', *inputs, '--output', str(archive)], check=True)
        return archive

    def mutate(self, archive, replace=None, extra=None):
        result = self.work / 'mutated.zip'
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(result, 'w') as dst:
            for info in src.infolist():
                data = src.read(info)
                if replace and info.filename == replace[0]:
                    data = replace[1]
                dst.writestr(info, data)
            if extra:
                info = zipfile.ZipInfo(extra[0])
                info.external_attr = extra[2] << 16
                dst.writestr(info, extra[1])
        return result

    def test_official_source_hashes_and_native_old_packages(self):
        manifest = json.loads(PRIMARY.with_suffix('.json').read_text())
        for path, digest in manifest['files_sha256'].items():
            self.assertEqual(build.sha256(PRIMARY / path), digest)
        hashes = compat.verify_source(compat.SOURCE_SHA, lambda p: (PRIMARY / p).read_bytes())
        self.assertEqual(len(hashes), len(compat.FORMAT_FILES))
        for arch in ('x86_64', 'aarch64'):
            names = build.inspect_archive(self.archive(arch), arch, compat.FORMAT)
            self.assertTrue(set(compat.BINARIES) <= set(names))
            self.assertIn(compat.EMACS, names)
            self.assertIn('tmp/icons.zip', names)

    def test_source_or_format_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exact unpatched PC source SHA'):
            compat.verify_source('a' * 40, lambda p: (PRIMARY / p).read_bytes())
        with self.assertRaisesRegex(ValueError, 'source format mismatch'):
            compat.verify_source(compat.SOURCE_SHA, lambda p: b'changed' if p == 'src/config.bzl' else (PRIMARY / p).read_bytes())

    def test_current_format_and_schema_stay_strict(self):
        snapshot = self.work / 'current-snapshot'
        build.snapshot(self.recipe, snapshot)
        self.assertEqual(json.loads((snapshot / 'dictionary.json').read_text())['schema_version'], 2)
        archive = self.archive()
        with zipfile.ZipFile(archive) as old:
            header = old.read(compat.BINARIES[0])
        current = self.work / 'current.zip'
        entries = [(path, header, 0o100755) for path in build.BINARIES]
        entries += [(build.XML, b'<component/>', 0o100644)]
        entries += [(prefix + 'icon.png', b'PNG fixture', 0o100644) for prefix in build.ICON_DIRS]
        with zipfile.ZipFile(current, 'w') as zf:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                zf.writestr(info, data)
        build.inspect_archive(current, 'aarch64')
        with self.assertRaises(ValueError):
            build.inspect_archive(self.mutate(current, extra=(compat.EMACS, header, 0o100755)), 'aarch64')
        with self.assertRaises(ValueError):
            build.inspect_archive(current, 'aarch64', compat.FORMAT)

    def test_mixed_unknown_traversal_and_symlink_payloads_rejected(self):
        archive = self.archive()
        for extra in (('usr/lib/mozkey/mozc_server', b'x', 0o100755),
                      ('usr/share/icons/mozc/unknown.svg', b'x', 0o100644),
                      ('usr/share/icons/mozc/unknown/', b'', 0o40755),
                      ('../outside', b'x', 0o100644),
                      ('usr/share/icons/mozc/link', b'x', 0o120777)):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build.inspect_archive(self.mutate(archive, extra=extra), 'aarch64', compat.FORMAT)
        with self.assertRaises(ValueError):
            build.inspect_archive(archive, 'aarch64')

    def test_all_native_elfs_including_emacs_and_component_are_checked(self):
        archive = self.archive()
        with zipfile.ZipFile(archive) as zf:
            for name in (*compat.BINARIES, compat.EMACS):
                data = bytearray(zf.read(name))
                data[18:20] = (62).to_bytes(2, 'little')
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'wrong ELF architecture'):
                    build.inspect_archive(self.mutate(archive, replace=(name, data)), 'aarch64', compat.FORMAT)
            xml = zf.read(compat.XML).replace(b'com.google.IBus.Mozc', b'com.google.IBus.Mozkey')
        with self.assertRaisesRegex(ValueError, 'component paths/identity'):
            build.inspect_archive(self.mutate(archive, replace=(compat.XML, xml)), 'aarch64', compat.FORMAT)

    def test_inner_and_installed_icons_must_match(self):
        archive = self.archive()
        with self.assertRaisesRegex(ValueError, 'icon bytes differ'):
            build.inspect_archive(self.mutate(archive, replace=('usr/share/icons/mozc/dictionary.svg', b'changed')), 'aarch64', compat.FORMAT)

    def test_separate_locked_snapshot_and_source_remain_unchanged(self):
        before = {p: build.sha256(self.source / p) for p in self.trees[self.source]}
        snapshot = self.work / 'snapshot'
        build.snapshot(self.source, snapshot, self.recipe)
        data = json.loads((snapshot / 'dictionary.json').read_text())
        self.assertEqual(data['schema_version'], 3)
        self.assertEqual(data['source_sha'], compat.SOURCE_SHA)
        self.assertEqual(data['preparation']['source_sha'], RECIPE_SHA)
        self.assertEqual(data['notices']['files']['documents/DICTIONARY_NOTICES.md']['source_sha'], RECIPE_SHA)
        self.assertEqual(data['notices']['files']['documents/LICENSE']['source_sha'], compat.SOURCE_SHA)
        digest = build.sha256(snapshot / 'dictionary.json')
        build.restore(self.source, snapshot, digest)
        self.assertFalse((self.source / 'tools/dictionary/locks/daily_sources.json').exists())
        for p, fingerprint in before.items():
            self.assertEqual(build.sha256(self.source / p), fingerprint)
        for name in build.DICTIONARIES:
            self.assertEqual(build.sha256(self.source / build.GENERATED / name), data['files'][name])
        data['preparation']['source_sha'] = '0' * 40
        build.write_json(snapshot / 'dictionary.json', data)
        with self.assertRaisesRegex(ValueError, 'preparation differs'):
            build.validate_snapshot(self.source, snapshot, build.sha256(snapshot / 'dictionary.json'))

    def test_package_provenance_preserves_original_archive(self):
        snapshot = self.work / 'snapshot'
        build.snapshot(self.source, snapshot, self.recipe)
        digest = build.sha256(snapshot / 'dictionary.json')
        build.restore(self.source, snapshot, digest)
        source_zip = self.source / 'src/bazel-bin/unix/mozc.zip'
        source_zip.parent.mkdir(parents=True)
        shutil.copyfile(self.archive(), source_zip)
        with mock.patch.dict(os.environ, {'EXPECTED_DICTIONARY_SHA256': digest}), mock.patch.object(
                build.subprocess, 'check_output', return_value=b'gcc\tfixture\n'):
            output = self.work / 'output'
            build.package(self.source, output, snapshot, 'aarch64')
        data = json.loads((output / 'metadata.json').read_text())
        self.assertEqual(data['source_sha'], compat.SOURCE_SHA)
        self.assertEqual(data['archive_sha256'], build.sha256(source_zip))
        self.assertEqual(data['package_format']['name'], compat.FORMAT)
        self.assertEqual(data['dictionary']['preparation']['source_sha'], RECIPE_SHA)
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(build.sha256(output / name), digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
