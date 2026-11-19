#!/usr/bin/env python3
"""Run the public gate and rejection probes in a disposable synthetic repository."""
import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def run(command, cwd, expected_error=None):
    print('COMMAND: ' + shlex.join(map(str, command)), flush=True)
    result = subprocess.run(list(map(str, command)), cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    if expected_error is not None:
        # Preserve the intentional whitespace failure verbatim as a JSON value,
        # without introducing that bad whitespace into the transcript itself.
        print(json.dumps({'stdout': result.stdout}, ensure_ascii=False), flush=True)
    else:
        print(result.stdout, end='', flush=True)
    print(f'EXIT_CODE: {result.returncode}', flush=True)
    if expected_error is None:
        if result.returncode:
            raise SystemExit(result.returncode)
    elif result.returncode == 0 or expected_error not in result.stdout:
        raise RuntimeError(f'expected rejection containing {expected_error!r}')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: build_linux_gate_test.py /absolute/path/to/actionlint')
    original = Path(__file__).resolve().parents[2]
    actionlint = Path(sys.argv[1]).resolve()
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    with tempfile.TemporaryDirectory(prefix='mozkey-public-gate-') as temporary:
        root = Path(temporary) / 'repo'
        own = root / 'tools/minamorl'
        own.mkdir(parents=True)
        for path in (original / 'tools/minamorl').glob('build*'):
            destination = own / path.name
            if path.is_dir():
                shutil.copytree(path, destination)
            else:
                shutil.copy2(path, destination)
        workflow = '.github/workflows/minamorl-linux-build.yml'
        (root / workflow).parent.mkdir(parents=True)
        shutil.copy2(original / workflow, root / workflow)
        for name in ('build_package.py', 'build_icons.py'):
            destination = root / 'src/unix' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / 'src/unix' / name, destination)
        run(['git', 'init', '-q', root], root)
        run(['git', 'add', '--all'], root)
        check = [sys.executable, own / 'build_linux_primary.py', '--check-whitespace']
        primary = own / 'build_linux_ea2e9f5c_sources'

        readme = primary / 'src/data/dictionary_oss/README.txt'
        original_bytes = readme.read_bytes()
        trimmed = original_bytes.rstrip() + b'\n'
        if trimmed == original_bytes:
            raise RuntimeError('the expected upstream README EOF whitespace is absent')
        readme.write_bytes(trimmed)
        run(check, root, 'primary source bytes differ: src/data/dictionary_oss/README.txt')
        readme.write_bytes(original_bytes)

        manifest = primary.with_suffix('.json')
        original_manifest = manifest.read_bytes()
        manifest.write_bytes(original_manifest + b'\n')
        run(check, root, 'manifest differs from the reviewed fixed hash')
        manifest.write_bytes(original_manifest)

        extra = primary / 'unexpected.txt'
        extra.write_text('unknown primary file\n')
        run(check, root, 'primary source payload file set differs')
        extra.unlink()

        nested = own / 'build_linux_owned_fixture/nested.py'
        nested.parent.mkdir()
        nested.write_text('value = 1 \n')
        run(check, root, 'owned whitespace check failed: tools/minamorl/build_linux_owned_fixture/nested.py')
        nested.write_text('value = 1\n')
        # Leave this untracked nested directory in place for the complete gate.
        # The source README is restored exactly, including its original blank EOF.
        if readme.read_bytes() != original_bytes:
            raise RuntimeError('primary README bytes were not restored')
        run(['bash', own / 'build_linux_check.sh', actionlint], root)


if __name__ == '__main__':
    main()
