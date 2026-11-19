"""Serialize and bind CI command results without including command output."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePath
import re
import secrets
import shlex
import time

from corrected_inputs import ALLOWED_PATHS, DICTIONARY, FORMAT_PATHS, SOURCE_CLASS


KIND = "mozkey-corrected-distribution-v1"
TEST_TARGET = "//unix/ibus:ibus_mozc_test"
SYSTEMS = {"x86_64-linux", "aarch64-linux"}
MEMBERS = {
    "tests": "ci/tests.json",
    "body_build": "ci/body_build.json",
    "package_build": "ci/package_build.json",
    "package_validation": "ci/package-validation.json",
}
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHA256 = re.compile(r"[0-9a-f]{64}")
RECIPE_FIELDS = {
    "repository", "source_sha", "tree_sha", "command", "files_sha256",
    "original_build_sha256", "overlay_build_sha256", "encoder_sha256",
}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def trace_id():
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    return "".join(ULID_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def check_command(kind, argv):
    if kind == "package_validation":
        if (len(argv) < 4 or argv[1] != "-B" or PurePath(argv[2]).name != "corrected_build.py"
                or argv[3] != "validate-package"):
            raise ValueError("Expected the package validation command")
        return
    operation = "test" if kind == "tests" else "build"
    if len(argv) < 3 or PurePath(argv[0]).name not in {"bazel", "bazelisk"} or argv[1] != operation:
        raise ValueError(f"Expected a Bazel {operation} command for {kind}")
    if kind == "tests" and TEST_TARGET not in argv[2:]:
        raise ValueError("The required IBus regression target was absent")
    if kind == "package_build" and "//:package" not in argv[2:]:
        raise ValueError("The canonical Bazel package target was absent")
    if "--config=release_build" not in argv or {"--nobuild", "--build=false"}.intersection(argv):
        raise ValueError("Expected an executing release build command")


def command_evidence(kind, target, result, source_before, source_after, trace, job, measurement=None):
    """Bind a finished subprocess to source measurements taken around its execution.

    The caller supplies public Bazel argv only and verifies the dataset before
    and after the command. No stdout, stderr, environment or URL credentials
    are serialized here.
    """
    member = MEMBERS[kind]
    if not isinstance(result.args, (list, tuple)):
        raise ValueError("An executed argv is required")
    if type(result.returncode) is not int:
        raise ValueError("An actual command exit code is required")
    check_command(kind, result.args)
    if not SHA256.fullmatch(source_before) or not SHA256.fullmatch(source_after):
        raise ValueError("Expected SHA-256 fingerprints of the tracked sources")
    record = {
        **job,
        "source_sha": target["source_sha"],
        "tree_sha": target["tree_sha"],
        "dataset_sha256": DICTIONARY["dataset_sha256"],
        "command": shlex.join(result.args),
        "exit_code": result.returncode,
        "source_before_sha256": source_before,
        "source_after_sha256": source_after,
    }
    if kind == "tests":
        record["target"] = TEST_TARGET
    document = {
        **(measurement or {}),
        **record,
        "kind": kind,
        "target": TEST_TARGET if kind == "tests" else None,
        "executed": True,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": "info" if result.returncode == 0 and source_before == source_after else "error",
        "trace_id": trace,
        "msg": "CI command returned",
    }
    content = json_bytes(document)
    return {**record, "member_path": member, "sha256": hashlib.sha256(content).hexdigest()}, content


def verified_evidence(kind, metadata_record, content, target, job):
    if metadata_record["member_path"] != MEMBERS[kind]:
        raise ValueError("Unexpected CI evidence member path")
    if hashlib.sha256(content).hexdigest() != metadata_record["sha256"]:
        raise ValueError("CI evidence bytes do not match their recorded SHA-256")
    document = json.loads(content)
    expected_target = TEST_TARGET if kind == "tests" else None
    if document["target"] != expected_target or metadata_record.get("target") != expected_target:
        raise ValueError("Unexpected CI evidence target")
    if document["kind"] != kind or document["executed"] is not True:
        raise ValueError("Expected evidence from execution of the specified CI stage")
    for key in ("source_sha", "tree_sha", "dataset_sha256", "command", "exit_code",
                "source_before_sha256", "source_after_sha256", *job):
        if document[key] != metadata_record[key]:
            raise ValueError(f"CI evidence does not bind {key}")
    if any(document[key] != value for key, value in job.items()):
        raise ValueError("CI evidence is from a different native job or recipe run")
    if document["source_sha"] != target["source_sha"] or document["tree_sha"] != target["tree_sha"]:
        raise ValueError("CI evidence does not bind the corrected source")
    if document["dataset_sha256"] != DICTIONARY["dataset_sha256"]:
        raise ValueError("CI evidence does not bind the reused dataset")
    check_command(kind, shlex.split(document["command"]))
    if type(document["exit_code"]) is not int or document["exit_code"] != 0:
        raise ValueError("The CI stage did not exit successfully")
    if (not SHA256.fullmatch(document["source_before_sha256"])
            or document["source_before_sha256"] != document["source_after_sha256"]):
        raise ValueError("The source changed during the CI stage")
    timestamp = datetime.fromisoformat(document["ts"].replace("Z", "+00:00"))
    if (timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(None)
            or document["level"] != "info" or not isinstance(document["msg"], str) or not document["msg"]
            or not re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", document["trace_id"])):
        raise ValueError("CI evidence lacks its UTC log identity")
    return document["source_before_sha256"]


def job_identity(system, run_id, job_id, recipe):
    return {
        "system": system, "ci_run_id": run_id, "ci_job_id": job_id,
        "recipe_source_sha": recipe["source_sha"], "recipe_tree_sha": recipe["tree_sha"],
    }


def build_metadata(system, target, recipe, package_format, run_id, job_id, evidence):
    if system not in SYSTEMS or not all(isinstance(x, str) and x for x in (run_id, job_id)):
        raise ValueError("Expected a pinned CI system and actual run/job IDs")
    if RECIPE_FIELDS - recipe.keys() or any(not recipe[key] for key in RECIPE_FIELDS):
        raise ValueError("Missing measured build recipe provenance")
    if target["files_sha256"].keys() != ALLOWED_PATHS:
        raise ValueError("Expected all five corrected-source blob digests")
    if (package_format["name"] != "ea2-ibus-corrected-mozc-v1"
            or package_format["source_sha"] != target["source_sha"]
            or package_format["source_files_sha256"].keys() != FORMAT_PATHS):
        raise ValueError("Package format does not describe the corrected target")
    for path in ALLOWED_PATHS & FORMAT_PATHS:
        if target["files_sha256"][path] != package_format["source_files_sha256"][path]:
            raise ValueError("Corrected source and package-format digests disagree")
    job = job_identity(system, run_id, job_id, recipe)
    fingerprints = {
        verified_evidence(kind, *evidence[kind], target, job)
        for kind in MEMBERS
    }
    if len(fingerprints) != 1:
        raise ValueError("CI stages did not observe the same source bytes")
    return {
        "schema_version": 2,
        "kind": KIND,
        "source_class": SOURCE_CLASS,
        "system": system,
        "target": target,
        "dictionary": DICTIONARY.copy(),
        "recipe": recipe,
        "package_format": package_format,
        "provenance": {
            **job,
            **{kind: evidence[kind][0] for kind in MEMBERS if kind != "tests"},
        },
        "tests": evidence["tests"][0],
    }
