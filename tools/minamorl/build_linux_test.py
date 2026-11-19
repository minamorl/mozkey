#!/usr/bin/env python3
"""Exercise fail-closed handoff with synthetic files, without Bazel/network/desktop."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import build_linux_metadata as build


class ProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.root.mkdir()
        lock = self.root / 'tools/dictionary/locks/daily_sources.json'
        lock.parent.mkdir(parents=True)
        lock.write_text('{"fixture":true}\n')
        self.generated = self.root / build.GENERATED
        self.generated.mkdir(parents=True)
        for name, relative in build.NOTICES.items():
            notice = self.root / relative
            notice.parent.mkdir(parents=True, exist_ok=True)
            notice.write_text(f'target source notice fixture: {name}\n')
        # Commit only real inputs; generated files are not part of the source tree.
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.git('add', 'tools', *build.NOTICES.values())
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        for name in build.DICTIONARIES:
            (self.generated / name).write_text(f'{name}\tfixture\n')
        self.snapshot = Path(self.tmp.name) / 'snapshot'
        build.snapshot(self.root, self.snapshot)
        self.digest = build.sha256(self.snapshot / 'dictionary.json')

    def git(self, *args):
        return build.git(self.root, *args)

    def test_plan_rejects_refs_and_duplicate_control(self):
        sha = 'a' * 40
        self.assertEqual(build.plan(sha, ''), [{'label': 'source', 'sha': sha}])
        self.assertEqual(len(build.plan(sha, 'b' * 40)), 2)
        for bad in ('main', '-a', 'A' * 40, sha + '\n', '$(false)'):
            with self.assertRaises(ValueError):
                build.plan(bad, '')
        with self.assertRaises(ValueError):
            build.plan(sha, sha)

    def test_restore_exact_snapshot_and_refuse_overwrite(self):
        for name in build.DICTIONARIES:
            (self.generated / name).unlink()
        build.restore(self.root, self.snapshot, self.digest)
        for name in build.DICTIONARIES:
            self.assertEqual(build.sha256(self.generated / name), build.sha256(self.snapshot / name))
        with self.assertRaisesRegex(ValueError, 'replace existing'):
            build.restore(self.root, self.snapshot, self.digest)

    def test_snapshot_includes_target_notices_and_verified_checksums(self):
        import json
        data = json.loads((self.snapshot / 'dictionary.json').read_text())
        source_sha = self.git('rev-parse', 'HEAD').decode().strip()
        self.assertEqual(data['schema_version'], 2)
        self.assertEqual(data['notices']['source_sha'], source_sha)
        for name, relative in build.NOTICES.items():
            document = self.snapshot / 'documents' / name
            self.assertEqual(document.read_bytes(), self.git('show', f'{source_sha}:{relative}'))
            self.assertEqual(data['notices']['files'][f'documents/{name}'],
                             {'source_path': relative, 'sha256': build.sha256(document)})
        covered = set()
        for line in (self.snapshot / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            covered.add(name)
            self.assertEqual(digest, build.sha256(self.snapshot / name))
        self.assertTrue({f'documents/{name}' for name in build.NOTICES} <= covered)
        self.assertIn('dictionary.json', covered)
        build.validate_snapshot(self.root, self.snapshot, self.digest)

    def test_snapshot_rejects_tampered_missing_extra_or_linked_notices(self):
        document = self.snapshot / 'documents/DICTIONARY_NOTICES.md'
        original = document.read_bytes()
        document.write_bytes(b'tampered notice')
        with self.assertRaisesRegex(ValueError, 'snapshot notice hash mismatch'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)
        document.unlink()
        with self.assertRaisesRegex(ValueError, 'snapshot notice file set differs'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)
        document.symlink_to('SudachiDict-LEGAL.txt')
        with self.assertRaisesRegex(ValueError, 'snapshot notice missing/not regular'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)
        document.unlink()
        document.write_bytes(original)
        extra = self.snapshot / 'documents/unexpected.txt'
        extra.write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'snapshot notice file set differs'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)
        extra.unlink()
        (self.snapshot / 'SHA256SUMS').write_text('tampered checksums\n')
        with self.assertRaisesRegex(ValueError, 'snapshot checksum manifest differs'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)

    def test_tampered_manifest_payload_and_extra_file_fail(self):
        with self.assertRaisesRegex(ValueError, 'manifest hash'):
            build.validate_snapshot(self.root, self.snapshot, '0' * 64)
        file = self.snapshot / build.DICTIONARIES[0]
        original = file.read_bytes()
        file.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'dictionary hash'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)
        file.write_bytes(original)
        (self.snapshot / 'unexpected').write_text('no')
        with self.assertRaisesRegex(ValueError, 'unexpected snapshot'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)

    def test_changed_config_or_lock_fails(self):
        config = self.root / 'src/config.bzl'
        config.parent.mkdir(exist_ok=True)
        config.write_text('CHANGED = True\n')
        self.git('add', 'src/config.bzl')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'changed config')
        with self.assertRaisesRegex(ValueError, 'build config differs'):
            build.validate_snapshot(self.root, self.snapshot, self.digest)

    def test_ui_only_revision_keeps_dictionary_identity(self):
        ui = self.root / 'src/renderer/fixture.cc'
        ui.parent.mkdir(parents=True)
        ui.write_text('// UI-only difference\n')
        self.git('add', 'src/renderer/fixture.cc')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'UI change')
        data = build.validate_snapshot(self.root, self.snapshot, self.digest)
        self.assertNotEqual(data['source_sha'], self.git('rev-parse', 'HEAD').decode().strip())

    def test_separate_ci_recipe_can_be_added_without_changing_build_inputs(self):
        recipe = self.root / 'tools/minamorl/build_fixture.py'
        recipe.parent.mkdir(parents=True)
        recipe.write_text('# CI tooling runs from the separate recipe checkout\n')
        self.git('add', 'tools/minamorl/build_fixture.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'add separate CI recipe')
        build.validate_snapshot(self.root, self.snapshot, self.digest)

    def test_dictionary_generator_code_change_is_rejected(self):
        generator = self.root / 'src/dictionary/gen_filtered_dictionary.py'
        generator.parent.mkdir(parents=True)
        generator.write_text('# fixture generator enabled\n')
        self.git('add', 'src/dictionary/gen_filtered_dictionary.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'dictionary generator')
        matching = Path(self.tmp.name) / 'generator-snapshot'
        build.snapshot(self.root, matching)
        digest = build.sha256(matching / 'dictionary.json')
        build.validate_snapshot(self.root, matching, digest)
        generator.write_text('# fixture generator disabled\n')
        self.git('add', 'src/dictionary/gen_filtered_dictionary.py')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'change dictionary generator')
        with self.assertRaisesRegex(ValueError, 'build config differs'):
            build.validate_snapshot(self.root, matching, digest)

    def test_renderer_target_build_configuration_change_is_rejected(self):
        config = self.root / 'src/renderer/qt/BUILD.bazel'
        config.parent.mkdir(parents=True)
        config.write_text('defines = ["ENABLE_QT_RENDERER"]\n')
        self.git('add', 'src/renderer/qt/BUILD.bazel')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'renderer config')
        matching = Path(self.tmp.name) / 'matching-snapshot'
        build.snapshot(self.root, matching)
        digest = build.sha256(matching / 'dictionary.json')
        build.validate_snapshot(self.root, matching, digest)
        config.write_text('defines = []\n')
        self.git('add', 'src/renderer/qt/BUILD.bazel')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'remove renderer define')
        with self.assertRaisesRegex(ValueError, 'build config differs'):
            build.validate_snapshot(self.root, matching, digest)

    def test_bazelignore_and_tracked_lock_additions_and_changes_are_rejected(self):
        for index, relative in enumerate(('src/.bazelignore', 'src/MODULE.bazel.lock')):
            with self.subTest(path=relative):
                before = Path(self.tmp.name) / f'before-{index}'
                build.snapshot(self.root, before)
                before_digest = build.sha256(before / 'dictionary.json')
                config = self.root / relative
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text('fixture initial configuration\n')
                self.git('add', '-f', relative)
                self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                         '-c', 'commit.gpgsign=false', 'commit', '-qm', 'add Bazel config')
                with self.assertRaisesRegex(ValueError, 'build config differs'):
                    build.validate_snapshot(self.root, before, before_digest)
                matching = Path(self.tmp.name) / f'matching-{index}'
                build.snapshot(self.root, matching)
                digest = build.sha256(matching / 'dictionary.json')
                build.validate_snapshot(self.root, matching, digest)
                config.write_text('fixture changed configuration\n')
                self.git('add', '-f', relative)
                self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                         '-c', 'commit.gpgsign=false', 'commit', '-qm', 'change Bazel config')
                with self.assertRaisesRegex(ValueError, 'build config differs'):
                    build.validate_snapshot(self.root, matching, digest)

    def test_package_records_archive_identity_without_environment_secrets(self):
        import json
        import os
        archive = self.root / 'src/bazel-bin/unix/mozc.zip'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(self.archive().read_bytes())
        source_sha = self.git('rev-parse', 'HEAD').decode().strip()
        # A recipe/working-tree notice is not the target revision's document.
        (self.root / build.NOTICES['DICTIONARY_NOTICES.md']).write_text('uncommitted notice that must not ship\n')
        output = Path(self.tmp.name) / 'package'
        environment = {
            'EXPECTED_DICTIONARY_SHA256': self.digest, 'EXPECTED_SOURCE_SHA': source_sha,
            'GITHUB_SHA': 'b' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
            'SHOULD_NEVER_APPEAR': 'fixture-secret-marker',
        }
        actual_check_output = subprocess.check_output

        def check_output(argv):
            if argv[0] == 'dpkg-query':
                return b'gcc\tfixture-version\n'
            return actual_check_output(argv)

        with mock.patch.dict(os.environ, environment), mock.patch.object(
                build.subprocess, 'check_output', side_effect=check_output):
            build.package(self.root, output, self.snapshot, 'x86_64')
        data = json.loads((output / 'metadata.json').read_text())
        self.assertEqual(data['source_sha'], source_sha)
        self.assertEqual(data['archive_sha256'], build.sha256(output / 'mozc.zip'))
        self.assertEqual(data['dictionary_sha256'], self.digest)
        self.assertNotIn('fixture-secret-marker', (output / 'metadata.json').read_text())
        self.assertEqual(data['notices']['source_sha'], source_sha)
        for name, relative in build.NOTICES.items():
            target = output / 'documents' / name
            self.assertEqual(target.read_bytes(), self.git('show', f'{source_sha}:{relative}'))
            self.assertEqual(data['notices']['files'][f'documents/{name}'],
                             {'source_path': relative, 'sha256': build.sha256(target)})
        checksum_files = set()
        for line in (output / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            checksum_files.add(name)
            self.assertEqual(digest, build.sha256(output / name))
        self.assertTrue({f'documents/{name}' for name in build.NOTICES} <= checksum_files)
        (self.generated / build.DICTIONARIES[0]).write_text('changed after build')
        with mock.patch.dict(os.environ, environment):
            with self.assertRaisesRegex(ValueError, 'build dictionary changed'):
                build.package(self.root, Path(self.tmp.name) / 'refused', self.snapshot, 'x86_64')

    def test_source_notices_reject_missing_and_empty_documents(self):
        notice = self.root / build.NOTICES['DICTIONARY_NOTICES.md']
        notice.write_text('')
        self.git('add', build.NOTICES['DICTIONARY_NOTICES.md'])
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'empty notice')
        revision = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError, 'source notice empty'):
            build.source_notices(self.root, revision)
        empty_output = Path(self.tmp.name) / 'empty-notice-snapshot'
        with self.assertRaisesRegex(ValueError, 'source notice empty'):
            build.snapshot(self.root, empty_output)
        self.assertFalse(empty_output.exists())
        notice.unlink()
        self.git('add', '-u')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'missing notice')
        revision = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError, 'source notice missing'):
            build.source_notices(self.root, revision)
        missing_output = Path(self.tmp.name) / 'missing-notice-snapshot'
        with self.assertRaisesRegex(ValueError, 'source notice missing'):
            build.snapshot(self.root, missing_output)
        self.assertFalse(missing_output.exists())
        notice.symlink_to('missing-source-notice')
        self.git('add', build.NOTICES['DICTIONARY_NOTICES.md'])
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'symlink notice')
        linked_output = Path(self.tmp.name) / 'linked-notice-snapshot'
        with self.assertRaisesRegex(ValueError, 'source notice missing/not regular'):
            build.snapshot(self.root, linked_output)
        self.assertFalse(linked_output.exists())

    def archive(self, arch='x86_64', extra=None, omit=None, elf_type=3, extras=()):
        path = Path(self.tmp.name) / 'mozc.zip'
        header = bytearray(64)
        header[:7] = b'\x7fELF\x02\x01\x01'
        header[16:18] = elf_type.to_bytes(2, 'little')
        header[18:20] = {'x86_64': 62, 'aarch64': 183}[arch].to_bytes(2, 'little')
        header[20:24] = (1).to_bytes(4, 'little')
        header[52:54] = (64).to_bytes(2, 'little')
        entries = [(n, bytes(header), 0o100755) for n in build.BINARIES if n != omit]
        entries += [(build.XML, b'<component/>', 0o100644)]
        entries += [(prefix + 'icon.png', b'fixture', 0o100644) for prefix in build.ICON_DIRS]
        if extra:
            entries.append(extra)
        entries.extend(extras)
        with zipfile.ZipFile(path, 'w') as zf:
            for name, contents, mode in entries:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = mode << 16
                zf.writestr(entry, contents)
        return path

    def test_archive_accepts_native_layout_for_each_arch(self):
        for arch in ('x86_64', 'aarch64'):
            for elf_type in (2, 3):
                names = build.inspect_archive(self.archive(arch, elf_type=elf_type), arch)
                self.assertIn('usr/share/ibus/component/mozkey.xml', names)

    def test_elf_without_executable_or_pie_type_is_rejected(self):
        for elf_type in (0, 1, 4):
            with self.subTest(elf_type=elf_type), self.assertRaisesRegex(ValueError, 'ET_EXEC/ET_DYN'):
                build.inspect_archive(self.archive(elf_type=elf_type), 'x86_64')

    def test_existing_packager_icon_subdirectories_are_accepted(self):
        repo = Path(build.__file__).resolve().parents[2]
        work = Path(self.tmp.name) / 'packager'
        work.mkdir()
        png = work / 'product_icon_32bpp-128.png'
        png.write_bytes(b'fixture PNG')
        svg = work / 'dictionary.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>')
        icons = work / 'icons.zip'
        subprocess.run([sys.executable, str(repo / 'src/unix/build_icons.py'),
                        '--inputs', str(png), str(svg), '--output', str(icons)], check=True)
        inputs = [str(icons)]
        with zipfile.ZipFile(self.archive()) as zf:
            for name in (*build.BINARIES, build.XML):
                target = work / ('ibus_mozc' if name == build.BINARIES[-1] else Path(name).name)
                target.write_bytes(zf.read(name))
                target.chmod(0o755 if name in build.BINARIES else 0o644)
                inputs.append(str(target))
        package = work / 'packaged.zip'
        subprocess.run([sys.executable, str(repo / 'src/unix/build_package.py'),
                        '--inputs', *inputs, '--output', str(package)], check=True)
        names = build.inspect_archive(package, 'x86_64')
        for variant in ('white', 'outlined'):
            self.assertIn(f'usr/share/icons/mozkey/{variant}/', names)
            self.assertIn(f'usr/share/icons/mozkey/{variant}/dictionary.svg', names)
        with zipfile.ZipFile(package) as zf:
            extracted = work / 'extracted'
            zf.extractall(extracted)
        self.assertTrue((extracted / 'usr/share/icons/mozkey/white/dictionary.svg').is_file())

    def test_wrong_elf_missing_binary_and_unknown_layout_fail(self):
        with self.assertRaisesRegex(ValueError, 'wrong ELF'):
            build.inspect_archive(self.archive('aarch64'), 'x86_64')
        with self.assertRaisesRegex(ValueError, 'missing package'):
            build.inspect_archive(self.archive(omit=build.BINARIES[0]), 'x86_64')
        with self.assertRaisesRegex(ValueError, 'unexpected ZIP file'):
            build.inspect_archive(self.archive(extra=('usr/lib/mozc/mozc_server', b'x', 0o100755)), 'x86_64')

    def test_zip_traversal_symlink_and_duplicate_fail(self):
        for entry in (('../escape', b'x', 0o100644),
                      ('usr/share/icons/mozkey/link', b'../target', 0o120777),
                      ('usr/share/icons/mozkey/special', b'x', 0o010755),
                      ('usr/share/icons/mozkey-evil/', b'', 0o40755),
                      ('usr\\share\\evil', b'x', 0o100644)):
            with self.assertRaises(ValueError):
                build.inspect_archive(self.archive(extra=entry), 'x86_64')
        path = self.archive()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'a') as zf:
                zf.writestr(build.XML, b'duplicate')
        with self.assertRaisesRegex(ValueError, 'duplicate ZIP'):
            build.inspect_archive(path, 'x86_64')

    def test_normalized_zip_duplicates_and_file_directory_conflicts_are_rejected(self):
        root = 'usr/share/icons/mozkey/'
        cases = (
            # Same destination, different raw names and types.
            ((root + 'white', b'x', 0o100644), (root + 'white/', b'', 0o40755)),
            # Implicit directory collides with a file, without a directory entry.
            ((root + 'white', b'x', 0o100644), (root + 'white/icon.svg', b'x', 0o100644)),
            # Dot components/repeated separators alias an existing file or dir.
            ((root + 'white/icon.svg', b'x', 0o100644), (root + 'white/./icon.svg', b'x', 0o100644)),
            ((root + 'white/', b'', 0o40755), (root + 'white//', b'', 0o40755)),
        )
        for pair in cases:
            for entries in (pair, pair[::-1]):
                with self.subTest(entries=entries), self.assertRaisesRegex(
                        ValueError, 'normalized ZIP destination|file/directory conflict'):
                    build.inspect_archive(self.archive(extras=entries), 'x86_64')


if __name__ == '__main__':
    unittest.main(verbosity=2)
