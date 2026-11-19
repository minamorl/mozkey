#!/usr/bin/env python3
"""Strict dictionary handoff and allowlisted Linux package provenance (stdlib only)."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
import zipfile


DICTIONARIES = (
    'dic-nico-pixiv-delta.txt', 'koyasi-syntax-guard.txt',
    'mozcdic-ut-daily.txt', 'mozcdic-ut-personal-names-daily.txt',
)
GENERATED = 'src/data/dictionary_koyasi/generated/profiled'
# A UI-only control/fix pair must share dictionary/packager inputs verbatim.
# All Bazel definitions are included separately, including per-target BUILD files.
INPUTS = (
    'tools/dictionary', 'src/data', 'src/dictionary', 'src/data_manager',
    'src/.bazelrc', 'src/.bazeliskrc',
    'src/MODULE.bazel', 'src/bazel', 'src/config.bzl', 'src/BUILD.bazel',
    'src/build_defs.bzl', 'src/unix/BUILD.bazel', 'src/unix/build_package.py',
    'src/unix/build_icons.py',
)
BINARIES = (
    'usr/lib/mozkey/mozc_server', 'usr/lib/mozkey/mozc_tool',
    'usr/lib/mozkey/mozc_renderer', 'usr/lib/ibus-mozkey/ibus-engine-mozkey',
)
XML = 'usr/share/ibus/component/mozkey.xml'
ICON_DIRS = ('usr/share/ibus-mozkey/', 'usr/share/icons/mozkey/')
# Bundle the target revision's notices with dictionary and binary artifacts. The Apache
# text is shared with the existing Windows installer, not a Windows runtime.
NOTICES = {
    'DICTIONARY_NOTICES.md': 'src/data/installer/DICTIONARY_NOTICES.md',
    'SudachiDict-LEGAL.txt': 'src/data/installer/SudachiDict-LEGAL.txt',
    'credits_en.html': 'src/data/installer/credits_en.html',
    'Mozc-dictionary-README.txt': 'src/data/dictionary_oss/README.txt',
    'THIRD_PARTY_NOTICES.md': 'THIRD_PARTY_NOTICES.md',
    'LICENSE': 'LICENSE',
    'LICENSE-2.0.txt': 'src/win32/installer/zenz_runtime/licenses/Apache-2.0.txt',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def inputs_sha(root):
    entries = set(git(root, 'ls-tree', '-r', '-z', 'HEAD', '--', *INPUTS, *NOTICES.values()).split(b'\0'))
    for entry in git(root, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
        if not entry:
            continue
        path = PurePosixPath(entry.split(b'\t', 1)[1].decode())
        # Include Bazel ignore/rc/version files, per-target definitions and
        # tracked MODULE.bazel.lock files (even though this repo ignores new locks).
        if ('bazel' in path.name or path.suffix == '.bzl'
                or path.name in ('BUILD', 'WORKSPACE')
                or (path.parts[0] == 'src' and path.suffix in (
                    '.py', '.sh', '.ps1', '.bat', '.rb', '.pl', '.awk'))):
            entries.add(entry)
    entries.discard(b'')
    return hashlib.sha256(b'\0'.join(sorted(entries)) + b'\0').hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def plan(source, control):
    result = []
    for label, value in [('source', source), ('control', control)]:
        if label == 'control' and not value:
            continue
        require(re.fullmatch('[0-9a-f]{40}', value), 'full lowercase commit SHA required')
        require(value != source or label == 'source', 'control must differ from source')
        result.append({'label': label, 'sha': value})
    return result


def snapshot(root, output):
    source_sha = git(root, 'rev-parse', 'HEAD').decode().strip()
    # This snapshot is the first public artifact, independently of later builds.
    notices = source_notices(root, source_sha)
    output.mkdir()
    files = {}
    for name in DICTIONARIES:
        src = root / GENERATED / name
        require(src.is_file() and not src.is_symlink() and src.stat().st_size > 0,
                f'missing/empty dictionary: {name}')
        shutil.copyfile(src, output / name)
        files[name] = sha256(output / name)
    lock = root / 'tools/dictionary/locks/daily_sources.json'
    shutil.copyfile(lock, output / 'daily_sources.json')
    notice_metadata = write_notices(output, notices)
    write_json(output / 'dictionary.json', {
        'schema_version': 2, 'profile': 'daily',
        'source_sha': source_sha,
        'build_inputs_sha256': inputs_sha(root),
        'input_lock_sha256': sha256(lock), 'files': files,
        'notices': {'source_sha': source_sha, 'files': notice_metadata},
    })
    (output / 'SHA256SUMS').write_text(checksums(output))
    validate_snapshot(root, output, sha256(output / 'dictionary.json'))


def validate_snapshot(root, directory, expected):
    require(re.fullmatch('[0-9a-f]{64}', expected), 'snapshot SHA256 required')
    require(sha256(directory / 'dictionary.json') == expected, 'snapshot manifest hash mismatch')
    data = json.loads((directory / 'dictionary.json').read_text())
    require(data['schema_version'] == 2 and data['profile'] == 'daily', 'unknown dictionary profile/schema')
    require(data['build_inputs_sha256'] == inputs_sha(root), 'control/fix dictionary or build config differs')
    require(set(data['files']) == set(DICTIONARIES), 'dictionary manifest file set differs')
    require({p.name for p in directory.iterdir()} == set(DICTIONARIES) | {
        'dictionary.json', 'daily_sources.json', 'documents', 'SHA256SUMS'},
            'unexpected snapshot payload')
    lock_hash = sha256(root / 'tools/dictionary/locks/daily_sources.json')
    require(lock_hash == data['input_lock_sha256'] == sha256(directory / 'daily_sources.json'), 'dictionary lock mismatch')
    for name in DICTIONARIES:
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                'dictionary must be a nonempty regular file')
        require(sha256(path) == data['files'][name], f'dictionary hash mismatch: {name}')
    require(re.fullmatch('[0-9a-f]{40}', data['source_sha']), 'snapshot source SHA required')
    notice_data = data['notices']
    require(notice_data['source_sha'] == data['source_sha'], 'snapshot notice source SHA mismatch')
    require(set(notice_data['files']) == {f'documents/{name}' for name in NOTICES},
            'snapshot notice manifest file set differs')
    documents = directory / 'documents'
    require(documents.is_dir() and not documents.is_symlink(), 'snapshot notice directory missing/not regular')
    require({p.name for p in documents.iterdir()} == set(NOTICES), 'snapshot notice file set differs')
    for name, source_path in NOTICES.items():
        path = documents / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 0,
                f'snapshot notice missing/not regular/empty: {name}')
        record = notice_data['files'][f'documents/{name}']
        require(record['source_path'] == source_path, 'snapshot notice source path differs')
        require(sha256(path) == record['sha256'], f'snapshot notice hash mismatch: {name}')
    checksum_path = directory / 'SHA256SUMS'
    require(checksum_path.is_file() and not checksum_path.is_symlink(), 'snapshot checksums missing/not regular')
    require(checksum_path.read_text() == checksums(directory), 'snapshot checksum manifest differs')
    return data


def restore(root, directory, expected):
    validate_snapshot(root, directory, expected)
    target = root / GENERATED
    target.mkdir(parents=True, exist_ok=True)
    for name in DICTIONARIES:
        require(not (target / name).exists(), 'refuse to replace existing generated dictionary')
    for name in DICTIONARIES:
        shutil.copyfile(directory / name, target / name)


def inspect_archive(archive, arch):
    machine = {'x86_64': 62, 'aarch64': 183}[arch]
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        names = [entry.filename for entry in entries]
        require(len(names) == len(set(names)), 'duplicate ZIP entry')
        destinations = {}
        for entry in entries:
            name = entry.filename
            path = PurePosixPath(name)
            require(not name.startswith('/') and '\\' not in name and '..' not in path.parts
                    and path.parts and '\0' not in entry.orig_filename,
                    f'unsafe ZIP path: {name}')
            # Extractors collapse repeated separators/dot components and strip
            # the trailing separator. Raw ZIP names are not destination IDs.
            destination = path.as_posix()
            require(destination not in destinations, f'duplicate normalized ZIP destination: {name}')
            destinations[destination] = entry.is_dir()
            mode = entry.external_attr >> 16
            require(not stat.S_ISLNK(mode), 'ZIP symlink forbidden')
            # The existing icon builder also emits white/ and outlined/ subtrees.
            if entry.is_dir():
                require(stat.S_ISDIR(mode), 'ZIP directory type missing/invalid')
                require(name.startswith(ICON_DIRS) or any(
                    p.startswith(name) for p in (*BINARIES, XML, *ICON_DIRS)),
                    f'unexpected ZIP directory: {name}')
            else:
                require(stat.S_ISREG(mode), 'ZIP payload must be a regular file')
                require(name in (*BINARIES, XML) or name.startswith(ICON_DIRS), f'unexpected ZIP file: {name}')
        files = {path for path, is_dir in destinations.items() if not is_dir}
        for destination in destinations:
            require(not any(parent.as_posix() in files for parent in PurePosixPath(destination).parents),
                    f'ZIP file/directory conflict: {destination}')
        require(set(BINARIES) | {XML} <= set(names), 'missing package binary/XML')
        for prefix in ICON_DIRS:
            require(any(n.startswith(prefix) and not n.endswith('/') for n in names), 'missing icons')
        require(zf.testzip() is None, 'corrupt ZIP payload')
        for name in BINARIES:
            with zf.open(name) as stream:
                header = stream.read(64)
            require(len(header) == 64 and header[:6] == b'\x7fELF\x02\x01', f'not a little-endian ELF64: {name}')
            require(header[6] == 1 and int.from_bytes(header[20:24], 'little') == 1
                    and int.from_bytes(header[52:54], 'little') == 64,
                    f'invalid ELF64 version/header size: {name}')
            require(int.from_bytes(header[16:18], 'little') in (2, 3),
                    f'not ELF ET_EXEC/ET_DYN: {name}')
            require(int.from_bytes(header[18:20], 'little') == machine, f'wrong ELF architecture: {name}')
            require(zf.getinfo(name).external_attr >> 16 & 0o111, f'not executable: {name}')
    return sorted(names)


def source_notices(root, revision):
    tree = git(root, 'ls-tree', '-r', '-z', revision, '--', *NOTICES.values())
    modes = {}
    for entry in tree.split(b'\0'):
        if entry:
            info, path = entry.split(b'\t', 1)
            modes[path.decode()] = info.split()[0]
    result = {}
    for name, path in NOTICES.items():
        require(modes.get(path) in (b'100644', b'100755'), f'required source notice missing/not regular: {path}')
        contents = git(root, 'show', f'{revision}:{path}')
        require(contents.strip(), f'required source notice empty: {path}')
        result[name] = contents
    return result


def write_notices(output, notices):
    (output / 'documents').mkdir()
    records = {}
    for name, contents in notices.items():
        (output / 'documents' / name).write_bytes(contents)
        records[f'documents/{name}'] = {
            'source_path': NOTICES[name], 'sha256': hashlib.sha256(contents).hexdigest(),
        }
    return records


def checksums(directory):
    files = sorted(p for p in directory.rglob('*') if p.is_file() and p != directory / 'SHA256SUMS')
    return ''.join(f'{sha256(p)}  {p.relative_to(directory).as_posix()}\n' for p in files)


def package(root, output, directory, arch):
    expected = os.environ['EXPECTED_DICTIONARY_SHA256']
    dictionary = validate_snapshot(root, directory, expected)
    # Validate bytes actually passed to Bazel, not just the downloaded snapshot.
    for name in DICTIONARIES:
        require(sha256(root / GENERATED / name) == dictionary['files'][name], 'build dictionary changed')
    source_sha = git(root, 'rev-parse', 'HEAD').decode().strip()
    require(source_sha == os.environ['EXPECTED_SOURCE_SHA'], 'built source SHA mismatch')
    archive = root / 'src/bazel-bin/unix/mozc.zip'
    names = inspect_archive(archive, arch)
    notices = source_notices(root, source_sha)
    output.mkdir()
    shutil.copyfile(archive, output / 'mozc.zip')
    shutil.copyfile(directory / 'dictionary.json', output / 'dictionary.json')
    shutil.copyfile(directory / 'daily_sources.json', output / 'daily_sources.json')
    notice_metadata = write_notices(output, notices)
    recipe_root = Path(__file__).resolve().parents[2]
    recipe_paths = ['tools/minamorl/build_linux.sh', 'tools/minamorl/build_linux_metadata.py',
                    '.github/workflows/minamorl-linux-build.yml']
    recipe = {p: sha256(recipe_root / p) for p in recipe_paths}
    metadata = {
        'schema_version': 1, 'source_sha': source_sha, 'archive_sha256': sha256(output / 'mozc.zip'),
        'arch': arch, 'dictionary_sha256': expected, 'dictionary': dictionary,
        'notices': {'source_sha': source_sha, 'files': notice_metadata},
        'build_recipe': {'files_sha256': recipe, 'command': 'bazelisk --batch --nosystem_rc --nohome_rc --output_user_root=$RUNNER_TEMP/bazel-output build package --config release_build --jobs=2',
                         'bazelisk_version': '1.27.0', 'bazel_version': '9.0.2',
                         'build_inputs_sha256': inputs_sha(root), 'workflow_sha': os.environ['GITHUB_SHA']},
        'runner_image': {k: os.environ.get(k, '') for k in ('ImageOS', 'ImageVersion')},
        'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    write_json(output / 'metadata.json', metadata)
    (output / 'archive-files.txt').write_text('\n'.join(names) + '\n')
    versions = subprocess.check_output(['dpkg-query', '--show', 'libibus-1.0-dev', 'qt6-base-dev', 'gcc', 'libc6', 'python3', 'pkg-config'])
    (output / 'package-versions.txt').write_bytes(versions)
    (output / 'SHA256SUMS').write_text(checksums(output))


def main():
    command, *args = sys.argv[1:]
    if command == 'plan':
        print(json.dumps(plan(*args), separators=(',', ':')))
    elif command == 'snapshot':
        snapshot(*map(Path, args))
    elif command == 'restore':
        restore(Path(args[0]), Path(args[1]), args[2])
    elif command == 'package':
        package(Path(args[0]), Path(args[1]), Path(args[2]), args[3])
    else:
        raise ValueError('unknown command')


if __name__ == '__main__':
    main()
