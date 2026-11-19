"""Verify the packaged bytes and bind the two artifact phases."""

import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tempfile
from zipfile import ZipFile

from corrected_diagnostics import checked_output
from corrected_evidence import MEMBERS, SHA256, build_metadata, json_bytes
from corrected_inputs import DICTIONARY, REPOSITORY, SOURCE_CLASS, InputError, sha256_file
from corrected_recipe import DOCUMENTS, RECIPE_MEMBERS, json_object


MACHINES = {"x86_64-linux": 62, "aarch64-linux": 183}
PACKAGE_ASSETS = {"tmp/icons.zip", "usr/share/ibus/component/mozc.xml",
                  "usr/share/emacs/site-lisp/emacs-mozc/mozc.el"}
OUTER_MEMBERS = {"mozc.zip", "dataset.json", "metadata.json", *MEMBERS.values(), *DOCUMENTS, *RECIPE_MEMBERS}


def zip_members(archive):
    files = {}
    seen = set()
    for info in archive.infolist():
        name = info.filename.removesuffix("/")
        if (not name or ":" in name or "\\" in name or name in seen
                or any(ord(char) < 32 for char in info.orig_filename)
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise InputError("ZIP contains an unsafe or duplicate path")
        file_type = stat.S_IFMT(info.external_attr >> 16)
        expected = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
        if file_type not in {0, expected} or info.flag_bits & 1:
            raise InputError("ZIP contains an unsupported entry type")
        seen.add(name)
        if not info.is_dir():
            files[name] = info
    for name in seen:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise InputError("ZIP contains a file in a directory position")
    return files


def elf_headers(archive, files, machine):
    binaries = {}
    for name, info in files.items():
        with archive.open(info) as member:
            measurement = measure_elf(member, info.file_size, machine)
            if measurement is not None:
                binaries[name] = measurement
    if len(binaries) != 5:
        raise InputError("Expected four core ELF files and the Emacs helper")
    return binaries


def measure_elf(member, size, machine):
    header = member.read(64)
    if header[:4] != b"\x7fELF":
        return None
    if len(header) != 64 or header[4:7] != b"\x02\x01\x01":
        raise InputError("ZIP contains a binary with an unexpected ELF identity")
    elf_type, elf_machine, version, _, phoff, shoff, _, ehsize, phentsize, phnum, shentsize, shnum, _ = (
        struct.unpack_from("<HHIQQQIHHHHHH", header, 16)
    )
    if elf_type not in {2, 3} or elf_machine != machine or version != 1 or ehsize != 64:
        raise InputError("ZIP contains a binary with an unexpected ELF identity")
    if phoff < ehsize or phentsize != 56 or not phnum or phoff + phentsize * phnum > size:
        raise InputError("ELF program header table is outside the file")
    if (shoff or shnum) and (shoff < ehsize or shentsize != 64 or not shnum or shoff + shentsize * shnum > size):
        raise InputError("ELF section header table is outside the file")
    member.seek(phoff)
    loads = []
    for _ in range(phnum):
        kind, _, offset, _, _, filesz, memsz, _ = struct.unpack("<IIQQQQQQ", member.read(56))
        if kind != 0 and (offset > size or filesz > size - offset):
            raise InputError("ELF program segment is outside the file")
        if kind == 1:
            if memsz < filesz:
                raise InputError("ELF PT_LOAD memory size is smaller than file size")
            loads.append({"offset": offset, "filesz": filesz, "memsz": memsz})
    if not loads:
        raise InputError("ELF has no PT_LOAD segment")
    return {"file_size": size, "elf_machine": elf_machine, "pt_load": loads}


def tool_output(command):
    return checked_output(command).decode("utf-8", errors="replace")


def inspect_elf(path):
    headers = tool_output(["readelf", "-W", "-l", "-d", str(path)])
    interpreter = re.search(r"\[Requesting program interpreter: ([^\]]+)\]", headers)
    needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", headers)
    if interpreter is None and not needed:
        return {"helper_linkage": "static", "interpreter": None, "needed": [], "closure": []}
    if interpreter is None or not interpreter[1].startswith("/"):
        raise InputError("A dynamic ELF has no absolute interpreter")
    tree = tool_output(["/usr/bin/python3", "/usr/bin/lddtree", "-l", str(path)])
    dependencies = [item for item in tree.splitlines() if item != str(path)]
    closure = set(dependencies)
    if not closure or not all(item.startswith("/") and Path(item).is_file() for item in closure):
        raise InputError("A dynamic ELF dependency did not resolve on the CI runner")
    if not Path(interpreter[1]).is_file():
        raise InputError("The ELF interpreter does not resolve on the CI runner")
    if set(needed) - {Path(item).name for item in closure}:
        raise InputError("A direct ELF dependency is missing from the measured closure")
    return {"helper_linkage": "dynamic", "interpreter": interpreter[1],
            "needed": needed, "closure": sorted(closure)}


def compare_packages(pc_zip, package, system, temp):
    """Use the fixed PC package as the inventory of retained assets."""
    with ZipFile(pc_zip) as baseline, ZipFile(package) as built:
        actual_files, actual, assets = compare_inventory(baseline, built, MACHINES[system])
        emacs = [name for name in actual if "emacs" in PurePosixPath(name).name.lower()]
        if len(emacs) != 1:
            raise InputError("Expected exactly one packaged Emacs helper")

        hashes, linkage = {}, {}
        with tempfile.TemporaryDirectory(dir=temp, prefix="elf-") as extracted:
            root = Path(extracted)
            for name in sorted(actual):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with built.open(actual_files[name]) as member, path.open("xb") as output:
                    shutil.copyfileobj(member, output)
                hashes[name] = sha256_file(path)
            for name in sorted(actual):
                linkage[name] = inspect_elf(root / name)
                actual[name].update({"sha256": hashes[name], **linkage[name]})
        return {
            "system": system,
            "elf_machine": MACHINES[system],
            "elf_files_sha256": hashes,
            **linkage[emacs[0]],
            "elf_measurements": actual,
            "assets_sha256": assets,
        }


def compare_inventory(baseline, built, machine):
    source_files, actual_files = zip_members(baseline), zip_members(built)
    if len(source_files) != 44 or actual_files.keys() != source_files.keys():
        raise InputError("The package does not retain the complete 44-member EA2 inventory")
    expected = elf_headers(baseline, source_files, 62)
    actual = elf_headers(built, actual_files, machine)
    if expected.keys() != actual.keys():
        raise InputError("The packaged ELF paths differ from the fixed EA2 package")
    other = source_files.keys() - expected.keys()
    icon_assets = {name for name in other if is_icon(name)}
    if len(icon_assets) != 36 or other != icon_assets | PACKAGE_ASSETS:
        raise InputError("The package does not contain the expected IBus, Emacs and icon assets")
    assets = {}
    for name in sorted(other):
        expected_bytes = baseline.read(source_files[name])
        actual_bytes = built.read(actual_files[name])
        if name == "tmp/icons.zip":
            if inner_icon_bytes(expected_bytes) != inner_icon_bytes(actual_bytes):
                raise InputError("The nested icon contents differ from EA2")
        elif not actual_bytes or actual_bytes != expected_bytes:
            raise InputError("A packaged non-ELF asset differs from EA2")
        assets[name] = hashlib.sha256(actual_bytes).hexdigest()
    return actual_files, actual, assets


def is_icon(name):
    parts = [part.lower() for part in PurePosixPath(name).parts]
    return "icons" in parts or "icon" in parts or parts[-1].endswith((".png", ".svg", ".xpm"))


def inner_icon_bytes(content):
    with ZipFile(io.BytesIO(content)) as archive:
        files = zip_members(archive)
        if not files:
            raise InputError("The nested icon archive is empty")
        return {name: hashlib.sha256(archive.read(info)).hexdigest() for name, info in files.items()}


def make_archive(archive, package, receipt, metadata, evidence, notices, recipe_files):
    if notices.keys() != DOCUMENTS or recipe_files.keys() != RECIPE_MEMBERS.keys() or evidence.keys() != MEMBERS.keys():
        raise InputError("Cannot assemble the fixed corrected archive member inventory")
    with ZipFile(archive, "x") as output:
        output.write(package, "mozc.zip")
        output.write(receipt, "dataset.json")
        output.writestr("metadata.json", json_bytes(metadata))
        for name, (_, content) in notices.items():
            output.writestr(name, content)
        for name, content in recipe_files.items():
            output.writestr(name, content)
        for record, content in evidence.values():
            output.writestr(record["member_path"], content)
    verify_archive(archive)


def verify_archive(archive):
    with ZipFile(archive) as output:
        members = zip_members(output)
        if len(output.infolist()) != len(OUTER_MEMBERS) or members.keys() != OUTER_MEMBERS:
            raise InputError("Corrected archive must contain its 17 fixed regular members")
        metadata_bytes = output.read(members["metadata.json"])
        metadata = json_object(metadata_bytes)
        provenance = metadata["provenance"]
        evidence = {
            kind: (metadata["tests"] if kind == "tests" else provenance[kind], output.read(members[name]))
            for kind, name in MEMBERS.items()
        }
        expected = build_metadata(metadata["system"], metadata["target"], metadata["recipe"],
                                  metadata["package_format"], provenance["ci_run_id"], provenance["ci_job_id"],
                                  evidence)
        if expected != metadata:
            raise InputError("Archive metadata does not match its native job and CI evidence")
        validation = json_object(evidence["package_validation"][1])
        abi = validation["abi"]
        machine = MACHINES[metadata["system"]]
        if (validation["embedded"] != {"elf_machine": machine, "dataset_sha256": DICTIONARY["dataset_sha256"],
                                        "pos_sha256": DICTIONARY["pos_sha256"]}
                or abi["system"] != metadata["system"] or abi["elf_machine"] != machine
                or len(abi["assets_sha256"]) != 39 or len(abi["elf_files_sha256"]) != 5
                or abi["elf_measurements"].keys() != abi["elf_files_sha256"].keys()):
            raise InputError("Package validation does not bind the measured package and five ELF files")
        for name, measurement in abi["elf_measurements"].items():
            if (measurement["sha256"] != abi["elf_files_sha256"][name] or measurement["elf_machine"] != machine
                    or not measurement["pt_load"] or any(
                        segment["offset"] < 0 or segment["filesz"] < 0 or segment["memsz"] < segment["filesz"]
                        or segment["offset"] + segment["filesz"] > measurement["file_size"]
                        for segment in measurement["pt_load"]
                    )):
                raise InputError("Package validation contains an invalid ELF measurement")
        if any(validation["overlay"][key] != metadata["recipe"][key] for key in RECIPE_MEMBERS.values()):
            raise InputError("Package validation overlay differs from the recorded build recipe")
        digests = {
            "dataset.json": DICTIONARY["dataset_receipt_sha256"],
            "mozc.zip": validation["package_sha256"],
            **{name: validation["notices"][name]["sha256"] for name in DOCUMENTS},
            **{name: metadata["recipe"][key] for name, key in RECIPE_MEMBERS.items()},
        }
        for name, digest in digests.items():
            with output.open(members[name]) as member:
                measured = hashlib.sha256()
                for data in iter(lambda: member.read(1024 * 1024), b""):
                    measured.update(data)
            if measured.hexdigest() != digest:
                raise InputError(f"Archive member {name} differs from its recorded bytes")
        return metadata, hashlib.sha256(metadata_bytes).hexdigest()


def artifact_endpoint(artifact_id):
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id):
        raise InputError("An actual positive Actions artifact ID is required")
    return f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"


def candidate_records(archive, downloaded, artifact_id, artifact_digest, job, source_sha):
    archive_url = f"https://api.github.com/{artifact_endpoint(artifact_id)}"
    if not SHA256.fullmatch(artifact_digest):
        raise InputError("Unexpected fixed Actions artifact digest")
    digest = sha256_file(archive)
    if digest != artifact_digest or digest != sha256_file(downloaded):
        raise InputError("Uploaded artifact bytes differ from the assembled outer ZIP")
    metadata, metadata_sha = verify_archive(downloaded)
    if (metadata["target"]["source_sha"] != source_sha
            or any(metadata["provenance"][key] != value for key, value in job.items())):
        raise InputError("Uploaded metadata does not describe this corrected run")
    return {
        "schema_version": 1,
        "kind": "mozkey-corrected-candidate-v1",
        "source_class": SOURCE_CLASS,
        "system": metadata["system"],
        "target_source_sha": metadata["target"]["source_sha"],
        "tree_sha": metadata["target"]["tree_sha"],
        "archive_url": archive_url,
        "archive_sha256": digest,
        "metadata_sha256": metadata_sha,
    }, {"ci_artifact_id": artifact_id, "archive_url": archive_url, "archive_sha256": digest}
