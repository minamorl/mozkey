"""Measure the corrected producer's fixed Git source and reused PC data."""

import ast
import hashlib
import os
from pathlib import Path
import re
import stat

from corrected_diagnostics import CommandFailure, run_command


REPOSITORY = "minamorl/mozkey"
BASE_SHA = "ea2e9f5cfb80fccf50caca1af097b2f0830be52a"
FORK_URL = f"https://github.com/{REPOSITORY}"
SOURCE_CLASS = "ea2-ibus-corrected-v1"
IBUS_BUILD = "src/unix/ibus/BUILD.bazel"
ALLOWED_PATHS = frozenset({
    IBUS_BUILD,
    "src/unix/ibus/mozc_engine.cc",
    "src/unix/ibus/property_handler.cc",
    "src/unix/ibus/property_handler.h",
    "src/unix/ibus/property_handler_test.cc",
})
FORMAT_PATHS = frozenset({
    "src/config.bzl",
    "src/unix/BUILD.bazel",
    "src/unix/build_package.py",
    "src/unix/build_icons.py",
    "src/.bazeliskrc",
    "src/data/dictionary_koyasi/BUILD.bazel",
    "src/data/dictionary_oss/BUILD.bazel",
    "src/unix/ibus/gen_mozc_xml.py",
    IBUS_BUILD,
})
DICTIONARY = {
    "source_sha": BASE_SHA,
    "pc_zip_url": (
        "https://github.com/minamorl/mozkey/releases/download/"
        "ci-pc-ea2e9f5c-data/mozkey-ea2e9f5c-linux.zip"
    ),
    "pc_zip_sha256": "2acd4614d9b95a3be4ca7b29bcee164d1b753f358a62441e50490c8fce533c30",
    "dataset_sha256": "9ef432c90b46b60aa7cd022176edc8e4b922c5855a1b6c8801a04e822683a4f8",
    "pos_sha256": "2e8e6a25423cea16a71eadd850e10936dfdd614d660680afe2b57713c9a9def4",
    "dataset_receipt_sha256": "34c39deb04c94d5aa5efa1ed858fff050d487b87d240eb1112e6c2ab4863d6f6",
}


class InputError(ValueError):
    """An input did not match its fixed identity."""


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_commit(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise InputError("A full lowercase 40-character commit SHA is required")
    return value


def git(source, *args, allowed=(0,)):
    result = run_command(["git", "--no-replace-objects", "-C", os.fspath(source), *args])
    if result.returncode not in allowed:
        raise CommandFailure(result)
    return result


def tree_entries(source, commit):
    entries = {}
    raw = git(source, "ls-tree", "-r", "-z", "--full-tree", commit).stdout
    for line in raw.split(b"\0")[:-1]:
        header, name = line.split(b"\t", 1)
        mode, kind, oid = header.split()
        entries[os.fsdecode(name)] = (mode, kind, oid)
    return entries


def blob_digests(source, entries, paths):
    result = {}
    for path in sorted(paths):
        mode, kind, oid = entries.get(path, (None, None, None))
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise InputError(f"Expected a regular Git blob at {path}")
        data = git(source, "cat-file", "blob", oid.decode("ascii")).stdout
        result[path] = hashlib.sha256(data).hexdigest()
    return result


def measure_target(source, source_sha):
    """Measure Git identity and path scope in a checkout fetched from the own fork.

    The caller verifies the fork fetch and the scope of any IBus BUILD edit.
    """
    require_commit(source_sha)
    head = git(source, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    if head != source_sha or source_sha == BASE_SHA:
        raise InputError("The checkout must be the requested corrected commit")
    ancestry = git(source, "merge-base", "--is-ancestor", BASE_SHA, source_sha, allowed=(0, 1))
    if ancestry.returncode != 0:
        raise InputError("Corrected source does not descend from EA2")

    base = tree_entries(source, BASE_SHA)
    target = tree_entries(source, source_sha)
    changed = sorted(path for path in base.keys() | target.keys() if base.get(path) != target.get(path))
    if not changed or set(changed) - ALLOWED_PATHS:
        raise InputError("Corrected source must differ from EA2 only on the allowed IBus paths")
    files = blob_digests(source, target, ALLOWED_PATHS)
    format_files = blob_digests(source, target, FORMAT_PATHS)
    if IBUS_BUILD in changed and target[IBUS_BUILD][0] != base[IBUS_BUILD][0]:
        raise InputError("The IBus regression change cannot change BUILD executable mode")

    tree = git(source, "rev-parse", "--verify", f"{source_sha}^{{tree}}").stdout.decode().strip()
    return {
        "repository": REPOSITORY,
        "source_sha": source_sha,
        "tree_sha": require_commit(tree),
        "base_sha": BASE_SHA,
        "changed_paths": changed,
        "files_sha256": files,
    }, {
        "name": "ea2-ibus-corrected-mozc-v1",
        "source_sha": source_sha,
        "source_files_sha256": format_files,
    }


def verify_fork_checkout(source, source_sha):
    require_commit(source_sha)
    remote = git(source, "remote", "get-url", "origin").stdout.decode().strip()
    if remote not in {FORK_URL, f"{FORK_URL}.git"}:
        raise InputError("The source was not checked out from the own fork")
    refs = git(source, "for-each-ref", f"--contains={source_sha}",
               "--format=%(refname)", "refs/remotes/origin/", "refs/tags/").stdout
    if not refs.strip():
        raise InputError("The corrected commit is not reachable from the fetched fork refs")


def regression_rule(source_text):
    """Separate the pinned regression rule from the rest of a BUILD file."""
    statements = []
    rule = None
    for statement in ast.parse(source_text).body:
        call = statement.value if isinstance(statement, ast.Expr) else None
        if not isinstance(call, ast.Call) or not any(
            keyword.arg == "name" and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "ibus_mozc_test" for keyword in call.keywords
        ):
            statements.append(ast.dump(statement))
            continue
        if (rule is not None or call.args or not isinstance(call.func, ast.Name)
                or call.func.id not in {"cc_test", "mozc_cc_test"}):
            raise InputError("Unsupported or ambiguous IBus regression rule")
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        if None in keywords or len(keywords) != len(call.keywords):
            raise InputError("The regression rule must have explicit unique attributes")
        rule = (call.func.id, keywords)
    return statements, rule


def string_list(value):
    if not isinstance(value, (ast.List, ast.Tuple)) or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts
    ):
        raise InputError("Changed regression attributes must be literal string lists")
    return [item.value for item in value.elts]


def verify_ibus_build(source, target):
    if IBUS_BUILD not in target["changed_paths"]:
        return
    before = git(source, "show", f"{BASE_SHA}:{IBUS_BUILD}").stdout.decode("utf-8")
    after = git(source, "show", f"{target['source_sha']}:{IBUS_BUILD}").stdout.decode("utf-8")
    if before == after:
        raise InputError("A mode-only change to the IBus BUILD file is outside the regression scope")
    base_other, base_rule = regression_rule(before)
    target_other, target_rule = regression_rule(after)
    if base_other != target_other or target_rule is None:
        raise InputError("IBus BUILD changes must be confined to the pinned regression rule")
    test_kind, attributes = target_rule
    old_kind, old_attributes = base_rule or (test_kind, {})
    if test_kind != old_kind:
        raise InputError("The existing regression test rule kind changed")
    for attribute in (attributes.keys() | old_attributes.keys()) - {"name", "srcs", "deps"}:
        if (attribute not in attributes or attribute not in old_attributes
                or ast.dump(attributes[attribute]) != ast.dump(old_attributes[attribute])):
            raise InputError("The regression rule may only add source and dependency inputs")
    for attribute in ("srcs", "deps"):
        old = old_attributes.get(attribute, ast.List(elts=[]))
        new = attributes.get(attribute, ast.List(elts=[]))
        if ast.dump(old) == ast.dump(new):
            continue
        if attribute == "srcs" and isinstance(old, ast.Call):
            if not all(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id == "mozc_select" and not node.args
                       and [keyword.arg for keyword in node.keywords] == ["linux"]
                       for node in (old, new)):
                raise InputError("The IBus test source selector changed")
            old, new = old.keywords[0].value, new.keywords[0].value
        old_list, new_list = string_list(old), string_list(new)
        additions = set(new_list) - set(old_list)
        if (len(set(new_list)) != len(new_list)
                or [item for item in new_list if item not in additions] != old_list):
            raise InputError("Regression rule removed or reordered an existing input")
        if attribute == "srcs" and additions != {"property_handler_test.cc"}:
            raise InputError("Unexpected regression test source addition")
        if attribute == "deps" and any(not item.startswith((":", "//")) for item in additions):
            raise InputError("Added regression dependencies must be workspace labels")


def source_fingerprint(source, source_sha):
    """Verify tracked worktree bytes and file modes against the expected Git tree."""
    require_commit(source_sha)
    source = Path(source).resolve(strict=True)
    actual = git(source, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode().strip()
    if actual != source_sha:
        raise InputError("The source commit changed")

    fingerprint = hashlib.sha256()
    for name, (mode, kind, oid) in tree_entries(source, source_sha).items():
        path = source / name
        if path.parent.resolve(strict=True) != path.parent:
            raise InputError("A tracked source path has a redirected parent")
        if kind == b"commit" and mode == b"160000":
            if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
                raise InputError("The fixed source has an unexpected materialized Git submodule")
            fingerprint.update(os.fsencode(name) + b"\0" + mode + b"\0" + oid)
            continue
        info = path.lstat()
        if kind == b"blob" and mode == b"120000" and stat.S_ISLNK(info.st_mode):
            data = os.fsencode(os.readlink(path))
            blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False)
            digest = hashlib.sha256(data)
        elif kind == b"blob" and stat.S_ISREG(info.st_mode) and mode in {b"100644", b"100755"}:
            if bool(info.st_mode & stat.S_IXUSR) != (mode == b"100755"):
                raise InputError("A tracked source file changed executable mode")
            blob = hashlib.sha1(f"blob {info.st_size}\0".encode(), usedforsecurity=False)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    blob.update(block)
                    digest.update(block)
        else:
            raise InputError("The worktree has an unexpected tracked source type")
        if blob.hexdigest().encode() != oid:
            raise InputError("Tracked worktree bytes differ from the fixed source")
        fingerprint.update(os.fsencode(name) + b"\0" + mode + b"\0" + digest.digest())
    return fingerprint.hexdigest()


def verify_pc_zip(path):
    if sha256_file(path) != DICTIONARY["pc_zip_sha256"]:
        raise InputError("The PC archive does not match its pinned SHA-256")


def verify_dataset(directory):
    """Match the opaque extractor's outputs against the fixed PC receipt and bytes."""
    files = {
        "mozc.data": ("dataset_sha256", 36327368),
        "pos_list.data": ("pos_sha256", None),
        "dataset.json": ("dataset_receipt_sha256", None),
    }
    for name, (key, size) in files.items():
        path = directory / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (size is not None and info.st_size != size):
            raise InputError(f"Unexpected reused PC input shape for {name}")
        if sha256_file(path) != DICTIONARY[key]:
            raise InputError(f"Unexpected reused PC input SHA-256 for {name}")
    return DICTIONARY.copy()
