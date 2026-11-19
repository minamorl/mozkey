import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "minamorl"))

import corrected_evidence as evidence
import corrected_inputs as inputs


class SourceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name).resolve()
        self.git("init", "--quiet", "--object-format=sha1")
        for name in inputs.ALLOWED_PATHS | inputs.FORMAT_PATHS:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name + "\n")
        self.commit()
        base = self.git("rev-parse", "HEAD")
        override = patch.object(inputs, "BASE_SHA", base)
        override.start()
        self.addCleanup(override.stop)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.source), "-c", "core.hooksPath=/dev/null",
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True,
        ).stdout.strip()

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_measures_the_full_five_path_and_nine_path_maps(self):
        changed = b"fixture changed BUILD\n"
        (self.source / inputs.IBUS_BUILD).write_bytes(changed)
        sha = self.commit()
        target, package = inputs.measure_target(self.source, sha)
        self.assertEqual([inputs.IBUS_BUILD], target["changed_paths"])
        self.assertEqual(inputs.ALLOWED_PATHS, target["files_sha256"].keys())
        self.assertEqual(inputs.FORMAT_PATHS, package["source_files_sha256"].keys())
        digest = hashlib.sha256(changed).hexdigest()
        self.assertEqual(digest, target["files_sha256"][inputs.IBUS_BUILD])
        self.assertEqual(digest, package["source_files_sha256"][inputs.IBUS_BUILD])

    def test_rejects_ea2_refs_and_changes_outside_the_allowlist(self):
        with self.assertRaises(inputs.InputError):
            inputs.measure_target(self.source, "HEAD")
        with self.assertRaises(inputs.InputError):
            inputs.measure_target(self.source, inputs.BASE_SHA)
        (self.source / "src" / "config.bzl").write_text("outside scope\n")
        with self.assertRaises(inputs.InputError):
            inputs.measure_target(self.source, self.commit())

    def test_rejects_non_descendant_commit(self):
        tree = self.git("rev-parse", "HEAD^{tree}")
        unrelated = self.git("commit-tree", tree, "-m", "unrelated")
        self.git("checkout", "--quiet", "--detach", unrelated)
        with self.assertRaisesRegex(inputs.InputError, "does not descend"):
            inputs.measure_target(self.source, unrelated)

    def test_rejects_symlink_replacement_even_on_an_allowed_path(self):
        (self.source / inputs.IBUS_BUILD).unlink()
        (self.source / inputs.IBUS_BUILD).symlink_to("../../../config.bzl")
        with self.assertRaisesRegex(inputs.InputError, "regular Git blob"):
            inputs.measure_target(self.source, self.commit())

    def test_fingerprint_rejects_uncommitted_source_change(self):
        self.assertRegex(inputs.source_fingerprint(self.source, inputs.BASE_SHA), r"^[0-9a-f]{64}$")
        (self.source / inputs.IBUS_BUILD).write_text("uncommitted change\n")
        with self.assertRaisesRegex(inputs.InputError, "differ from the fixed source"):
            inputs.source_fingerprint(self.source, inputs.BASE_SHA)


class DatasetTest(unittest.TestCase):
    def test_checks_data_shape_and_all_three_digest_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            data = directory / "mozc.data"
            with data.open("wb") as stream:
                stream.truncate(36327368)
            pos = directory / "pos_list.data"
            pos.write_bytes(b"fixture POS")
            receipt = directory / "dataset.json"
            receipt.write_bytes(b"{}\n")
            with patch.dict(inputs.DICTIONARY, {
                "dataset_sha256": inputs.sha256_file(data),
                "pos_sha256": inputs.sha256_file(pos),
                "dataset_receipt_sha256": inputs.sha256_file(receipt),
            }):
                self.assertEqual(inputs.DICTIONARY, inputs.verify_dataset(directory))
                for file in (data, pos, receipt):
                    with self.subTest(file=file.name):
                        original = file.read_bytes()
                        file.write_bytes(b"!" + original[1:])
                        with self.assertRaisesRegex(inputs.InputError, "SHA-256"):
                            inputs.verify_dataset(directory)
                        file.write_bytes(original)


class EvidenceTest(unittest.TestCase):
    target = {
        "source_sha": "1" * 40,
        "tree_sha": "2" * 40,
        "files_sha256": dict.fromkeys(inputs.ALLOWED_PATHS, "1" * 64),
    }
    package = {
        "name": "ea2-ibus-corrected-mozc-v1",
        "source_sha": "1" * 40,
        "source_files_sha256": dict.fromkeys(inputs.FORMAT_PATHS, "1" * 64),
    }
    recipe = dict.fromkeys(evidence.RECIPE_FIELDS, "fixture")
    fingerprint = "3" * 64
    job = evidence.job_identity("aarch64-linux", "123", "456", recipe)

    def command(self, kind, exit_code=0, after=None, before=None):
        argv = {
            "tests": ["bazel", "test", "--config=release_build", evidence.TEST_TARGET],
            "body_build": ["bazel", "build", "--config=release_build", "//example:body_fixture"],
            "package_build": ["bazel", "build", "--config=release_build", "//:package"],
            "package_validation": ["python3", "-B", "corrected_build.py", "validate-package"],
        }[kind]
        return evidence.command_evidence(
            kind, self.target, subprocess.CompletedProcess(argv, exit_code),
            before or self.fingerprint, after or self.fingerprint, evidence.trace_id(), self.job,
        )

    def test_binds_metadata_to_content_and_omits_upload_attributes(self):
        members = {kind: self.command(kind) for kind in evidence.MEMBERS}
        value = evidence.build_metadata(
            "aarch64-linux", self.target, self.recipe, self.package, "123", "456", members,
        )
        self.assertEqual(2, value["schema_version"])
        self.assertEqual("456", value["provenance"]["ci_job_id"])
        self.assertEqual(members["tests"][0], value["tests"])
        for kind, (record, content) in members.items():
            self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(kind, json.loads(content)["kind"])
        for field in ("archive_sha256", "ci_artifact_id", "manifest_sha256", "gates"):
            self.assertNotIn(field, evidence.json_bytes(value).decode())

    def test_rejects_failed_changed_or_tampered_evidence(self):
        good, content = self.command("tests")
        for pair in (self.command("tests", 1), self.command("tests", after="4" * 64),
                     (good, content + b" ")):
            with self.subTest(content=pair[0]["sha256"]):
                with self.assertRaises(ValueError):
                    evidence.verified_evidence("tests", *pair, self.target, self.job)

    def test_rejects_mismatched_stage_fingerprints(self):
        members = {kind: self.command(kind) for kind in evidence.MEMBERS}
        members["body_build"] = self.command("body_build", before="4" * 64, after="4" * 64)
        with self.assertRaisesRegex(ValueError, "same source bytes"):
            evidence.build_metadata(
                "aarch64-linux", self.target, self.recipe, self.package, "123", "456", members,
            )

    def test_rejects_missing_recipe_provenance(self):
        members = {kind: self.command(kind) for kind in evidence.MEMBERS}
        with self.assertRaisesRegex(ValueError, "Missing measured build recipe provenance"):
            evidence.build_metadata("x86_64-linux", self.target, {}, self.package, "123", "456", members)

    def test_rejects_bazel_build_as_test_evidence(self):
        result = subprocess.CompletedProcess(["bazel", "build", evidence.TEST_TARGET], 0)
        with self.assertRaisesRegex(ValueError, "Expected a Bazel test command"):
            evidence.command_evidence("tests", self.target, result, self.fingerprint,
                                      self.fingerprint, evidence.trace_id(), self.job)

    def test_rejects_skip_and_same_run_different_job(self):
        result = subprocess.CompletedProcess(["bazel", "test", "--config=release_build", "--nobuild", evidence.TEST_TARGET], 0)
        with self.assertRaisesRegex(ValueError, "executing release"):
            evidence.command_evidence("tests", self.target, result, self.fingerprint,
                                      self.fingerprint, evidence.trace_id(), self.job)
        record, content = self.command("tests")
        with self.assertRaisesRegex(ValueError, "different native job"):
            evidence.verified_evidence("tests", record, content, self.target, {**self.job, "ci_job_id": "999"})
