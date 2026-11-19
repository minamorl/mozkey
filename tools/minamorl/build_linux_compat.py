"""Pinned packaging adapter for the unpatched PC revision; no source patches."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import xml.etree.ElementTree as ET
import zipfile

SOURCE_SHA = 'ea2e9f5cfb80fccf50caca1af097b2f0830be52a'
FORMAT = 'upstream-ea2e9f5c-mozc'
FORMAT_FILES = (
    'src/config.bzl', 'src/unix/BUILD.bazel', 'src/unix/build_package.py',
    'src/unix/build_icons.py', 'src/.bazeliskrc',
    'src/data/dictionary_koyasi/BUILD.bazel', 'src/data/dictionary_oss/BUILD.bazel',
    'src/unix/ibus/gen_mozc_xml.py', 'src/unix/ibus/BUILD.bazel',
)
BINARIES = ('usr/lib/mozc/mozc_server', 'usr/lib/mozc/mozc_tool',
            'usr/lib/mozc/mozc_renderer', 'usr/lib/ibus-mozc/ibus-engine-mozc')
EMACS = 'usr/bin/mozc_emacs_helper'
XML = 'usr/share/ibus/component/mozc.xml'
ICON_DIRS = ('usr/share/ibus-mozc/', 'usr/share/icons/mozc/')
EXTRAS = (EMACS, 'usr/share/emacs/site-lisp/emacs-mozc/mozc.el', 'tmp/icons.zip')
BASE_ICONS = (
    'mozc.png', 'mozc.svg', 'direct.svg', 'alpha_full.svg', 'katakana_full.svg',
    'alpha_half.svg', 'katakana_half.svg', 'hiragana.svg', 'alpha_full.png',
    'alpha_half.png', 'direct.png', 'hiragana.png', 'katakana_full.png',
    'katakana_half.png', 'dictionary.png', 'dictionary.svg', 'properties.png',
    'properties.svg', 'tool.png', 'tool.svg',
)
ICONS = set(BASE_ICONS) | {f'{variant}/{name}.svg' for variant in ('white', 'outlined')
                          for name in ('dictionary', 'properties', 'tool')}
IBUS_ICONS = {name if name != 'mozc.png' else 'product_icon.png'
              for name in BASE_ICONS if name.endswith('.png')}
FILES = set(BINARIES) | set(EXTRAS) | {XML}
FILES |= {ICON_DIRS[1] + name for name in ICONS}
FILES |= {ICON_DIRS[0] + name for name in IBUS_ICONS}


def verify_source(revision, read_blob):
    if revision != SOURCE_SHA:
        raise ValueError('legacy format requires the exact unpatched PC source SHA')
    manifest = json.loads(Path(__file__).with_name('build_linux_ea2e9f5c_sources.json').read_text())
    if manifest['source_sha'] != SOURCE_SHA or manifest['repository'] != 'koyasi777/mozkey':
        raise ValueError('legacy primary-source manifest identity differs')
    hashes = {}
    for path in FORMAT_FILES:
        digest = hashlib.sha256(read_blob(path)).hexdigest()
        if digest != manifest['files_sha256'][path]:
            raise ValueError(f'legacy source format mismatch: {path}')
        hashes[path] = digest
    return hashes


def verify_payload(zf):
    """Check exact legacy payload and nested icon copy, without extracting it."""
    names = {entry.filename for entry in zf.infolist() if not entry.is_dir()}
    if names != FILES:
        raise ValueError(f'legacy payload differs: missing={sorted(FILES - names)} extra={sorted(names - FILES)}')
    component = ET.fromstring(zf.read(XML))
    engine = '/usr/lib/ibus-mozc/ibus-engine-mozc'
    if (component.tag != 'component' or component.findtext('name') != 'com.google.IBus.Mozc'
            or component.findtext('exec') != engine + ' --ibus'
            or component.findtext('textdomain') != 'ibus-mozc'
            or component.find('engines') is None
            or component.find('engines').get('exec') != engine + ' --xml'):
        raise ValueError('legacy IBus component paths/identity differ')
    with zipfile.ZipFile(io.BytesIO(zf.read('tmp/icons.zip'))) as icons:
        destinations = set()
        files = set()
        for entry in icons.infolist():
            path = PurePosixPath(entry.filename)
            normalized = path.as_posix()
            if (not path.parts or path.is_absolute() or '..' in path.parts
                    or '\\' in entry.filename or '\0' in entry.orig_filename
                    or normalized in destinations):
                raise ValueError('unsafe/duplicate nested legacy icon path')
            destinations.add(normalized)
            mode = entry.external_attr >> 16
            if entry.is_dir():
                if not stat.S_ISDIR(mode) or not any(n.startswith(normalized + '/') for n in ICONS):
                    raise ValueError('unknown nested legacy icon directory')
            else:
                if not stat.S_ISREG(mode) or normalized not in ICONS:
                    raise ValueError('unknown/nonregular nested legacy icon')
                if icons.read(entry) != zf.read(ICON_DIRS[1] + normalized):
                    raise ValueError('nested/installed legacy icon bytes differ')
                files.add(normalized)
        if files != ICONS or icons.testzip() is not None:
            raise ValueError('nested legacy icon manifest differs')
    for name in IBUS_ICONS:
        original = 'mozc.png' if name == 'product_icon.png' else name
        if zf.read(ICON_DIRS[0] + name) != zf.read(ICON_DIRS[1] + original):
            raise ValueError('legacy IBus icon copy differs')
