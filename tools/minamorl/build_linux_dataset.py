#!/usr/bin/env python3
"""Extract existing EA2 serialized data; never generate a dictionary."""
import hashlib
import json
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import zipfile
import urllib.request

SOURCE_SHA = 'ea2e9f5cfb80fccf50caca1af097b2f0830be52a'
PC_ZIP_SHA256 = '2acd4614d9b95a3be4ca7b29bcee164d1b753f358a62441e50490c8fce533c30'
PC_ZIP_URL = 'https://github.com/minamorl/mozkey/releases/download/ci-pc-ea2e9f5c-data/mozkey-ea2e9f5c-linux.zip'
DATA_SHA256 = '9ef432c90b46b60aa7cd022176edc8e4b922c5855a1b6c8801a04e822683a4f8'
POS_SHA256 = '2e8e6a25423cea16a71eadd850e10936dfdd614d660680afe2b57713c9a9def4'
RECEIPT_SHA256 = '34c39deb04c94d5aa5efa1ed858fff050d487b87d240eb1112e6c2ab4863d6f6'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def elf_symbol(data, marker, machine=62):
    require(data[:7] == b'\x7fELF\x02\x01\x01', 'ELF64 little endian required')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', data)
    require(header[2] == machine, 'embedded data ELF architecture mismatch')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, header[6] + i * header[11])
                for i in range(header[12])]
    matches = []
    for section in sections:
        if section[1] != 2:
            continue
        strings = sections[section[6]]
        strings = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', data, offset)
            name = strings[name:strings.find(b'\0', name)].decode()
            if marker not in name:
                continue
            require(info & 15 == 1 and index < len(sections), 'symbol must be an ELF object')
            # EA2 embed_file.py emits a uint64_t array, including zero-fill in
            # the final word. Never accept a prefix or a fractional extra word.
            require(size > 0 and size % 8 == 0 and value % 8 == 0,
                    'embedded uint64 array size/alignment mismatch')
            target = sections[index]
            relative = value - target[3]
            require(0 <= relative and relative + size <= target[5], 'symbol outside section')
            start = target[4] + relative
            require(start + size <= len(data), 'truncated embedded uint64 array')
            matches.append((data[start:start + size], {'symbol': name, 'file_offset': start, 'symbol_size': size}))
    require(len(matches) == 1, f'exactly one {marker} symbol required')
    return matches[0]


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        require(offset < len(data), 'truncated protobuf varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError('oversized protobuf varint')


def fields(data):
    offset = 0
    result = []
    while offset < len(data):
        tag, offset = varint(data, offset)
        require(tag >> 3 > 0, 'invalid protobuf field')
        if tag & 7 == 0:
            value, offset = varint(data, offset)
        elif tag & 7 == 2:
            size, offset = varint(data, offset)
            require(size <= len(data) - offset, 'truncated protobuf bytes')
            value = data[offset:offset + size]
            offset += size
        else:
            raise ValueError('unexpected dataset protobuf wire type')
        result.append((tag >> 3, value))
    return result


def dataset(array):
    candidates = []
    for padding in range(8):
        data = array[:-padding] if padding else array
        # DataSetWriter uses Util::SerializeUint64 (network/big-endian footer).
        if len(data) >= 43 and data[:7] == b'\xefMOZC\r\n' and int.from_bytes(data[-8:], 'big') == len(data):
            if hashlib.sha1(data[:-28]).digest() == data[-28:-8]:
                candidates.append(data)
    require(len(candidates) == 1, f'dataset footer length/SHA1 mismatch: size={len(array)} magic={array[:16].hex()} tail={array[-48:].hex()}')
    data = candidates[0]
    require(not any(array[len(data):]), 'dataset uint64 padding must be zero')
    size = int.from_bytes(data[-36:-28], 'big')
    require(0 < size <= len(data) - 43, 'invalid dataset metadata size')
    start = len(data) - 36 - size
    entries = {}
    previous = 7
    for number, entry in fields(data[start:start + size]):
        require(number == 1 and isinstance(entry, bytes), 'invalid dataset entry')
        values = fields(entry)
        require(len(values) == 3 and {key for key, _ in values} == {1, 2, 3}, 'invalid dataset fields')
        values = dict(values)
        name = values[1].decode('utf-8')
        offset, length = values[2], values[3]
        require(name not in entries and previous <= offset <= start and length <= start - offset,
                'dataset entry overlap/range/name mismatch')
        entries[name] = {'offset': offset, 'size': length,
                         'sha256': hashlib.sha256(data[offset:offset + length]).hexdigest()}
        previous = offset + length
    require({'dict', 'conn', 'pos_matcher', 'user_pos_token', 'user_pos_string', 'version'} <= set(entries),
            'required dataset chunks missing')
    version = entries['version']
    return data, entries, data[version['offset']:version['offset'] + version['size']].decode()


def string_array(array):
    require(len(array) >= 4, 'missing string array size')
    count = int.from_bytes(array[:4], 'little')
    end = 4 + count * 8
    require(0 < count <= 1000 and end <= len(array), 'invalid POS array size')
    strings = []
    for i in range(count):
        offset, size = struct.unpack_from('<II', array, 4 + i * 8)
        require(end <= offset and offset + size < len(array) and array[offset + size] == 0,
                'invalid POS string offset/terminator')
        strings.append(array[offset:offset + size].decode('utf-8'))
        end = offset + size + 1
    require(len(array) - end < 8 and not any(array[end:]) and '名詞' in strings,
            'invalid POS array padding/default')
    return array[:end], strings


def extract(archive, output):
    blob = archive.read_bytes()
    require(hashlib.sha256(blob).hexdigest() == PC_ZIP_SHA256, 'PC ZIP hash mismatch')
    with zipfile.ZipFile(archive) as zf:
        require(zf.testzip() is None, 'PC ZIP CRC mismatch')
        raw, symbol = elf_symbol(zf.read('usr/lib/mozc/mozc_server'), 'kOssMozcDataSet_data')
        data, entries, version = dataset(raw)
        pos_raw, pos_symbol = elf_symbol(zf.read('usr/lib/mozc/mozc_tool'), 'kPosArray_data')
        pos, names = string_array(pos_raw)
    output.mkdir()
    (output / 'mozc.data').write_bytes(data)
    (output / 'pos_list.data').write_bytes(pos)
    result = {'source_sha': SOURCE_SHA, 'pc_zip_sha256': PC_ZIP_SHA256,
              'dataset_sha256': hashlib.sha256(data).hexdigest(), 'dataset_bytes': len(data),
              'dataset_version': version, 'dataset_symbol': symbol, 'entries': entries,
              'pos_sha256': hashlib.sha256(pos).hexdigest(), 'pos_bytes': len(pos),
              'pos_symbol': pos_symbol, 'pos_names': names}
    (output / 'dataset.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    verify(output)
    print(json.dumps({key: value for key, value in result.items() if key not in ('entries', 'pos_names')}, ensure_ascii=False, indent=2))


def verify(directory):
    for name, expected in (('mozc.data', DATA_SHA256), ('pos_list.data', POS_SHA256), ('dataset.json', RECEIPT_SHA256)):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f'missing/nonregular reused data: {name}')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, f'reused data hash mismatch: {name}')
    data, entries, version = dataset((directory / 'mozc.data').read_bytes())
    pos, names = string_array((directory / 'pos_list.data').read_bytes())
    receipt = json.loads((directory / 'dataset.json').read_text())
    require(receipt['entries'] == entries and receipt['dataset_version'] == version
            and receipt['pos_names'] == names and receipt['source_sha'] == SOURCE_SHA
            and receipt['pc_zip_sha256'] == PC_ZIP_SHA256, 'PC data receipt/shape differs')
    return receipt


def overlay(source, directory, output):
    verify(directory)
    output.mkdir()
    package = output / 'data_manager/oss'
    package.mkdir(parents=True)
    original = source / 'src/data_manager/oss'
    for path in original.iterdir():
        require(path.is_file() and not path.is_symlink(), 'unexpected OSS source package entry')
        if path.name != 'BUILD.bazel':
            shutil.copyfile(path, package / path.name)
    text = (original / 'BUILD.bazel').read_text()
    start = text.index('\nmozc_dataset(\n')
    end = text.index('\nmozc_cc_library(\n', start)
    stanza = text[start:end]
    require(stanza.count('mozc_dataset(') == 1 and 'name = "mozc_dataset_for_oss"' in stanza,
            'unknown OSS dataset BUILD shape')
    replacements = '''
# Existing PC bytes only. No mozc_dataset macro invocation or dictionary tools.
exports_files(["mozc.data", "mozc_data.inc", "pos_list.inc"])
filegroup(name = "mozc_dataset_for_oss", srcs = ["mozc.data"])
filegroup(name = "mozc_dataset_for_oss@header", srcs = ["mozc_data.inc"])
filegroup(name = "mozc_dataset_for_oss@pos_list_header", srcs = ["pos_list.inc"])
'''
    (package / 'BUILD.bazel').write_text(text[:start] + replacements + text[end:])
    shutil.copyfile(directory / 'mozc.data', package / 'mozc.data')
    # Run the immutable source's standard byte-to-C++ encoder, not its data builder.
    encoder = source / 'src/build_tools/embed_file.py'
    for input_name, var, output_name in (('mozc.data', 'kOssMozcDataSet', 'mozc_data.inc'),
                                          ('pos_list.data', 'kPosArray', 'pos_list.inc')):
        subprocess.run([sys.executable, str(encoder), '--input', str(directory / input_name),
                        '--name', var, '--output', str(package / output_name)], check=True)
    return {'flag': '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%',
            'files_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in package.iterdir()},
            'original_build_sha256': hashlib.sha256((original / 'BUILD.bazel').read_bytes()).hexdigest(),
            'overlay_build_sha256': hashlib.sha256((package / 'BUILD.bazel').read_bytes()).hexdigest(),
            'encoder_sha256': hashlib.sha256(encoder.read_bytes()).hexdigest()}


def verify_package(archive, machine):
    with zipfile.ZipFile(archive) as zf:
        raw, symbol = elf_symbol(zf.read('usr/lib/mozc/mozc_server'), 'kOssMozcDataSet_data', machine)
        data, _, _ = dataset(raw)
        require(hashlib.sha256(data).hexdigest() == DATA_SHA256, 'built server does not contain the PC dataset')
        pos, _ = elf_symbol(zf.read('usr/lib/mozc/mozc_tool'), 'kPosArray_data', machine)
        pos, _ = string_array(pos)
        require(hashlib.sha256(pos).hexdigest() == POS_SHA256, 'built tool does not contain the PC POS data')
    return {'dataset_sha256': DATA_SHA256, 'pos_sha256': POS_SHA256, 'elf_machine': machine}


if __name__ == '__main__':
    if sys.argv[1] == '--verify-package':
        print(json.dumps(verify_package(Path(sys.argv[2]), int(sys.argv[3])), sort_keys=True))
    elif sys.argv[1] == '--probe-source':
        root = Path(sys.argv[2])
        for name in ('src/data_manager/oss/BUILD.bazel', 'src/data_manager/oss/oss_data_manager.cc',
                     'src/data_manager/oss/oss_data_manager.h', 'src/build_tools/embed_file.py'):
            with urllib.request.urlopen(f'https://raw.githubusercontent.com/koyasi777/mozkey/{SOURCE_SHA}/{name}', timeout=30) as response:
                data = response.read()
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            print(hashlib.sha256(data).hexdigest(), name)
    elif sys.argv[1] == '--overlay':
        result = overlay(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        extract(Path(sys.argv[1]), Path(sys.argv[2]))
