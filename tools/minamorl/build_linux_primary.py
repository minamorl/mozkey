#!/usr/bin/env python3
"""Fetch small, immutable upstream build inputs for offline compatibility fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

SOURCE_SHA = 'ea2e9f5cfb80fccf50caca1af097b2f0830be52a'
MANIFEST_SHA256 = '111b5338b0b0c6bb9004cd030e7d522141b97082054029eab88bb92a7824304f'
FILES = (
    '.github/workflows/linux.yaml',
    'src/config.bzl', 'src/unix/BUILD.bazel', 'src/unix/build_package.py',
    'src/unix/build_icons.py', 'src/.bazeliskrc',
    'src/data/dictionary_koyasi/BUILD.bazel', 'src/data/dictionary_oss/BUILD.bazel',
    'src/unix/ibus/gen_mozc_xml.py',
    'src/unix/ibus/BUILD.bazel', 'tools/dictionary/prepare_daily_dictionary.ps1',
    'docs/build_mozc_for_linux.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE',
    'src/data/installer/credits_en.html', 'src/data/dictionary_oss/README.txt',
    'src/win32/installer/zenz_runtime/licenses/Apache-2.0.txt',
)


def verify(root):
    manifest_path = root.with_suffix('.json')
    if (not manifest_path.is_file() or manifest_path.is_symlink()
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest() != MANIFEST_SHA256):
        raise ValueError('primary source manifest differs from the reviewed fixed hash')
    manifest = json.loads(manifest_path.read_text())
    if (manifest['repository'] != 'koyasi777/mozkey' or manifest['source_sha'] != SOURCE_SHA
            or set(manifest['files_sha256']) != set(FILES)):
        raise ValueError('primary source identity/file set differs')
    if not root.is_dir() or root.is_symlink():
        raise ValueError('primary source root must be a regular directory')
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f'nonregular primary source entry: {path}')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(FILES):
        raise ValueError('primary source payload file set differs')
    print(f'pristine primary source: koyasi777/mozkey@{SOURCE_SHA}', flush=True)
    for name in FILES:
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if digest != manifest['files_sha256'][name]:
            raise ValueError(f'primary source bytes differ: {name}')
        print(f'primary SHA256 {digest}  {name}', flush=True)


def check_whitespace():
    own = Path(__file__).resolve().parent
    repo = own.parents[1]
    primary = own / 'build_linux_ea2e9f5c_sources'
    # Only this exact, hash-verified pristine tree retains upstream whitespace.
    verify(primary)
    files = {repo / '.github/workflows/minamorl-linux-build.yml'}
    for path in own.glob('build*'):
        if path == primary:
            continue
        entries = [path, *path.rglob('*')] if path.is_dir() and not path.is_symlink() else [path]
        for entry in entries:
            if entry.is_symlink() or not (entry.is_dir() or entry.is_file()):
                raise ValueError(f'nonregular owned gate entry: {entry}')
            if entry.is_file():
                files.add(entry)
    for path in sorted(files):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'owned gate file missing/not regular: {path}')
        result = subprocess.run(['git', 'diff', '--no-index', '--check', '--', '/dev/null', str(path)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode != 0 and not (result.returncode == 1 and not result.stdout):
            raise ValueError(f'owned whitespace check failed: {path.relative_to(repo)}\n{result.stdout}')
    print('owned regular-file whitespace checks (recursive, including untracked): exit 0', flush=True)


def main():
    root = Path(__file__).with_name('build_linux_ea2e9f5c_sources')
    if sys.argv[1:] == ['--check-whitespace']:
        check_whitespace()
        return
    if sys.argv[1:]:
        raise ValueError('usage: build_linux_primary.py [--check-whitespace]')
    hashes = {}
    for name in FILES:
        url = f'https://raw.githubusercontent.com/koyasi777/mozkey/{SOURCE_SHA}/{name}'
        print(f'fetch {url}', flush=True)
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read()
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    manifest = {'repository': 'koyasi777/mozkey', 'source_sha': SOURCE_SHA, 'files_sha256': hashes}
    root.with_suffix('.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
