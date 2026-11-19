"""Connect the public EA2 dataset CLIs to the corrected build."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys

from corrected_diagnostics import checked_output
from corrected_evidence import SHA256
from corrected_inputs import (
    BASE_SHA, DICTIONARY, REPOSITORY, InputError, blob_digests, git,
    require_commit, sha256_file, tree_entries,
)


BASE_RECIPE = "2221a65432eda9c71c79ff08e8008dc218720fab"
DATASET_CLI = "tools/minamorl/build_linux_dataset.py"
METADATA_CLI = "tools/minamorl/build_linux_metadata.py"
SOURCES_LOCK = "tools/minamorl/build_linux_ea2e9f5c_sources.json"
RECIPE_PATHS = {
    DATASET_CLI,
    METADATA_CLI,
    SOURCES_LOCK,
    ".github/workflows/minamorl-linux-corrected.yml",
    "tools/minamorl/corrected_inputs.py",
    "tools/minamorl/corrected_evidence.py",
    "tools/minamorl/corrected_recipe.py",
    "tools/minamorl/corrected_archive.py",
    "tools/minamorl/corrected_build.py",
    "tools/minamorl/corrected_diagnostics.py",
}
OVERLAY_FLAG = "--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%"
ORIGINAL_BUILD = "src/data_manager/oss/BUILD.bazel"
ENCODER = "src/build_tools/embed_file.py"
PACKAGE_TARGET = "//:package"
RECIPE_MEMBERS = {
    "recipe/original-BUILD.bazel": "original_build_sha256",
    "recipe/overlay-BUILD.bazel": "overlay_build_sha256",
    "recipe/embed_file.py": "encoder_sha256",
}
DOCUMENTS = {f"documents/{name}" for name in (
    "DICTIONARY_NOTICES.md", "LICENSE", "LICENSE-2.0.txt", "Mozc-dictionary-README.txt",
    "SudachiDict-LEGAL.txt", "THIRD_PARTY_NOTICES.md", "credits_en.html",
)}


def public_cli(recipe, cli, *args):
    command = [sys.executable, "-B", str(recipe / cli), *map(str, args)]
    return checked_output(command, cwd=recipe)


def public_dataset_cli(recipe, *args):
    return public_cli(recipe, DATASET_CLI, *args)


def json_object(content):
    value = json.loads(content)
    if not isinstance(value, dict):
        raise InputError("Expected a JSON object from the public helper")
    return value


def verify_overlay(ea2, target, overlay, record):
    package = overlay / "data_manager" / "oss"
    if package.resolve(strict=True) != package or not package.is_dir():
        raise InputError("Missing or redirected dataset overlay package")
    if record.get("flag") != OVERLAY_FLAG:
        raise InputError("Unexpected dataset overlay registration flag")
    files = record.get("files_sha256")
    if not isinstance(files, dict) or "BUILD.bazel" not in files:
        raise InputError("The dataset overlay did not identify its files")
    if {path.name for path in package.iterdir()} != files.keys():
        raise InputError("The dataset overlay file set differs from its receipt")
    for name, digest in files.items():
        if (not isinstance(name, str) or name in {"", ".", ".."} or Path(name).name != name
                or not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            raise InputError("Invalid dataset overlay file receipt")
        path = package / name
        if not stat.S_ISREG(path.lstat().st_mode) or sha256_file(path) != digest:
            raise InputError("A dataset overlay file differs from its receipt")

    expected = {
        "original_build_sha256": sha256_file(ea2 / ORIGINAL_BUILD),
        "overlay_build_sha256": files["BUILD.bazel"],
        "encoder_sha256": sha256_file(ea2 / ENCODER),
    }
    if (sha256_file(target / ORIGINAL_BUILD) != expected["original_build_sha256"]
            or sha256_file(target / ENCODER) != expected["encoder_sha256"]):
        raise InputError("The target does not have the verified EA2 overlay declarations")
    if any(record.get(key) != value for key, value in expected.items()):
        raise InputError("The overlay receipt differs from the EA2 or generated source bytes")
    return expected


def measure_recipe(control, source_sha, bazel):
    require_commit(source_sha)
    current = git(control, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    if current != source_sha:
        raise InputError("The recipe checkout does not match the workflow commit")
    entries = tree_entries(control, source_sha)
    files = blob_digests(control, entries, RECIPE_PATHS)
    initial_paths = {DATASET_CLI, METADATA_CLI, SOURCES_LOCK}
    initial = blob_digests(control, tree_entries(control, BASE_RECIPE), initial_paths)
    if any(initial[path] != files[path] for path in initial_paths):
        raise InputError("The public dataset helper or legacy source lock changed from the recipe baseline")
    for path, digest in files.items():
        repository_file = control / path
        if (repository_file.resolve(strict=True) != repository_file
                or sha256_file(repository_file) != digest):
            raise InputError("The checked-out build recipe differs from its fixed Git bytes")
    tree = git(control, "rev-parse", "--verify", f"{source_sha}^{{tree}}").stdout.decode().strip()
    return {
        "repository": REPOSITORY,
        "source_sha": source_sha,
        "tree_sha": require_commit(tree),
        "command": f"{Path(bazel).name} build --config=release_build {OVERLAY_FLAG} --strip=never {PACKAGE_TARGET}",
        "files_sha256": files,
    }


def verify_embedded_dataset(recipe, package, machine):
    observed = json_object(public_dataset_cli(recipe, "--verify-package", package, machine))
    expected = {
        "elf_machine": machine,
        "dataset_sha256": DICTIONARY["dataset_sha256"],
        "pos_sha256": DICTIONARY["pos_sha256"],
    }
    if type(observed.get("elf_machine")) is not int or any(
        observed.get(key) != value for key, value in expected.items()
    ):
        raise InputError("The package does not embed the fixed PC dataset and POS bytes")
    return expected


def measure_notices(control, ea2, dataset, snapshot):
    """Take the seven existing EA2 notices from the exact-source public snapshot."""
    public_cli(control, METADATA_CLI, "pc-snapshot", ea2, snapshot, dataset)
    dictionary = json_object((snapshot / "dictionary.json").read_bytes())
    if (dictionary.get("schema_version") != 4 or dictionary.get("profile") != "pc-embedded"
            or dictionary.get("source_sha") != BASE_SHA or dictionary.get("pc_zip_url") != DICTIONARY["pc_zip_url"]
            or dictionary.get("dataset") != json_object((dataset / "dataset.json").read_bytes())):
        raise InputError("The notice snapshot does not bind the fixed EA2 source and PC dataset")
    notices = dictionary.get("notices", {})
    if not isinstance(notices, dict):
        raise InputError("Expected a notice provenance record")
    records = notices.get("files", {})
    if notices.get("source_sha") != BASE_SHA or not isinstance(records, dict) or len(records) != 7:
        raise InputError("Expected the seven existing EA2 notice records")
    captured = {}
    for name, record in sorted(records.items()):
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or relative.as_posix() != name
                or any(part in {".", ".."} for part in relative.parts)
                or not relative.parts or ":" in name or "\\" in name
                or any(ord(char) < 32 for char in name)):
            raise InputError("The notice receipt contains an unsafe relative path")
        path = snapshot / name
        if path.resolve(strict=True) != path or not stat.S_ISREG(path.lstat().st_mode):
            raise InputError("The notice receipt does not identify a regular snapshot file")
        content = path.read_bytes()
        if not isinstance(record, dict) or not content or hashlib.sha256(content).hexdigest() != record.get("sha256"):
            raise InputError("A notice differs from its verified snapshot digest")
        if not all(isinstance(record.get(field), str) and record[field]
                   for field in ("repository", "source_path", "source_sha")):
            raise InputError("A notice has no recorded source provenance")
        require_commit(record["source_sha"])
        member = f"documents/{relative.name}"
        if member not in DOCUMENTS or member in captured:
            raise InputError("Unexpected notice in the corrected document inventory")
        captured[member] = (record, content)
    return captured


def recipe_members(ea2, overlay, digests):
    members = {
        "recipe/original-BUILD.bazel": git(ea2, "show", f"{BASE_SHA}:{ORIGINAL_BUILD}").stdout,
        "recipe/overlay-BUILD.bazel": (overlay / "data_manager/oss/BUILD.bazel").read_bytes(),
        "recipe/embed_file.py": git(ea2, "show", f"{BASE_SHA}:{ENCODER}").stdout,
    }
    for name, content in members.items():
        if hashlib.sha256(content).hexdigest() != digests[RECIPE_MEMBERS[name]]:
            raise InputError("Recipe member does not match its designated Git blob or generated overlay")
    return members
