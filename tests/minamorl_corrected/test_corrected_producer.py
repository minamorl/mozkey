import hashlib
import io
from contextlib import redirect_stderr, redirect_stdout
import json
from pathlib import Path
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile, ZipInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "minamorl"))

import corrected_archive as archive
import corrected_build as build
import corrected_diagnostics as diagnostics
import corrected_evidence as evidence
import corrected_inputs as inputs
import corrected_recipe as recipe


class ProducerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_configured_cquery_and_all_bazel_invocations_share_the_release_options(self):
        control_sha = "1" * 40
        files = {
            name: inputs.sha256_file(self.write(name, b"recipe fixture"))
            for name in (recipe.DATASET_CLI, recipe.METADATA_CLI, recipe.SOURCES_LOCK)
        }
        with patch.object(recipe, "git", return_value=SimpleNamespace(stdout=control_sha.encode())), \
             patch.object(recipe, "tree_entries"), patch.object(recipe, "blob_digests", return_value=files):
            recorded_recipe = recipe.measure_recipe(self.root, control_sha, "bazelisk")

        checked = Mock(side_effect=[b"", b"@@//unix/ibus:engine\n//unix/ibus:engine\n//unix:server\n"])
        run = Mock(side_effect=lambda argv, **_: subprocess.CompletedProcess(argv, 0, stdout=b"{}"))
        args = SimpleNamespace(system="x86_64-linux", run_id="123", job_id="456", bazel="bazelisk", control_sha=control_sha)
        with patch.multiple(
            build, checked_output=checked, run_command=run, verify_pc_zip=Mock(),
            public_dataset_cli=Mock(return_value=b"{}"), verify_dataset=Mock(),
            measure_notices=Mock(return_value={}), verify_overlay=Mock(return_value={}),
            worktree_state=Mock(return_value="2" * 64), require_executed_test=Mock(),
            measure_recipe=Mock(return_value=recorded_recipe), emit=Mock(), build_metadata=Mock(),
            make_archive=Mock(), recipe_members=Mock(),
        ):
            build.build_outputs(args, evidence.trace_id(), self.root, self.root, self.root,
                                self.root, {"source_sha": "3" * 40, "tree_sha": "4" * 40}, {}, recorded_recipe)

        argv = [checked.call_args_list[1].args[0], *(call.args[0] for call in run.call_args_list[:3])]
        self.assertEqual(["cquery", "test", "build", "build"], [command[1] for command in argv])
        options = ["--config=release_build", f"--package_path={self.root / 'dataset-overlay'}:%workspace%",
                   "--strip=never", "--lockfile_mode=off"]
        for command in argv:
            self.assertEqual(options, command[2:6])
            self.assertEqual(["--lockfile_mode=off"], [arg for arg in command if arg.startswith("--lockfile_mode=")])
        self.assertEqual([
            "--universe_scope=//:package", "--noimplicit_deps", "--output=starlark",
            "--starlark:expr=str(target.label)", 'filter("^(@@)?//", kind("^cc_binary rule$", deps(//:package)))',
        ], argv[0][6:])
        self.assertEqual(["//unix/ibus:engine", "//unix:server"], argv[2][6:])
        self.assertEqual(["//:package"], argv[3][6:])
        actual_package = shlex.join(argv[-1]).replace(str(self.root / "dataset-overlay"), "$RUNNER_TEMP/dataset-overlay")
        self.assertEqual(actual_package, recorded_recipe["command"])

    def test_configured_cquery_rejects_empty_or_malformed_output(self):
        for output in (b"", b"\n", b"//unix:server (0eadbeef)\n", b"//:ok\n@@qt//:windows\n",
                       b"//unix/../ibus:engine\n", b"//unix//ibus:engine\n", b"//unix:engine\nnoise\n"):
            with self.subTest(output=output), patch.object(build, "checked_output", return_value=output):
                with self.assertRaisesRegex(inputs.InputError, "workspace binary targets"):
                    build.body_targets("bazelisk", self.root, recipe.release_build_options(recipe.OVERLAY_FLAG))

    def test_configured_cquery_rejects_target_patterns_even_with_valid_labels(self):
        for pattern in ("@@//unix/...:all", "//unix/...", "//unix/.../ibus:engine", "//unix/./ibus:engine",
                        "//unix/../ibus:engine", "//:all", "@@//unix:all", "//unix:all-targets", "//unix:*"):
            output = f"@@//unix/ibus:engine\n{pattern}\n".encode()
            with self.subTest(pattern=pattern), patch.object(build, "checked_output", return_value=output):
                with self.assertRaisesRegex(inputs.InputError, "workspace binary targets"):
                    build.body_targets("bazelisk", self.root, recipe.release_build_options(recipe.OVERLAY_FLAG))

    def test_configured_cquery_validates_dotted_package_components(self):
        options = recipe.release_build_options(recipe.OVERLAY_FLAG)
        valid = b"@@//:engine\n@@//unix/.hidden/ibus.v2:engine.debug\n//unix/...suffix:engine\n"
        with patch.object(build, "checked_output", return_value=valid):
            self.assertEqual(["//:engine", "//unix/...suffix:engine", "//unix/.hidden/ibus.v2:engine.debug"],
                             build.body_targets("bazelisk", self.root, options))
        for length in (1, 2, 3, 4, 5, 128):
            invalid = f"//unix/{'.' * length}/ibus:engine\n".encode()
            with self.subTest(length=length), patch.object(build, "checked_output", return_value=valid + invalid):
                with self.assertRaisesRegex(inputs.InputError, "workspace binary targets"):
                    build.body_targets("bazelisk", self.root, options)

    def test_overlay_checks_published_receipt_against_ea2_target_and_generated_bytes(self):
        ea2, target, overlay = (self.root / name for name in ("ea2", "target", "overlay"))
        source = {}
        for name in (recipe.ORIGINAL_BUILD, recipe.ENCODER):
            for root in (ea2, target):
                path = self.write(str(root.relative_to(self.root) / name), name.encode())
            source[name] = inputs.sha256_file(path)
        build_file = self.write("overlay/data_manager/oss/BUILD.bazel", b"fixture build")
        encoded = self.write("overlay/data_manager/oss/dataset.inc", b"fixture data")
        record = {
            "flag": recipe.OVERLAY_FLAG,
            "files_sha256": {path.name: inputs.sha256_file(path) for path in (build_file, encoded)},
            "original_build_sha256": source[recipe.ORIGINAL_BUILD],
            "overlay_build_sha256": inputs.sha256_file(build_file),
            "encoder_sha256": source[recipe.ENCODER],
        }
        expected = {key: record[key] for key in (
            "original_build_sha256", "overlay_build_sha256", "encoder_sha256",
        )}
        self.assertEqual(expected, recipe.verify_overlay(ea2, target, overlay, record))
        encoded.write_bytes(b"unexpected generated data")
        with self.assertRaisesRegex(inputs.InputError, "overlay file differs"):
            recipe.verify_overlay(ea2, target, overlay, record)
        encoded.write_bytes(b"fixture data")
        (target / recipe.ENCODER).write_text("changed encoder")
        with self.assertRaisesRegex(inputs.InputError, "verified EA2 overlay declarations"):
            recipe.verify_overlay(ea2, target, overlay, record)

    def test_embedded_dataset_verification_rejects_wrong_abi_or_pos(self):
        observed = {
            "elf_machine": 183,
            "dataset_sha256": inputs.DICTIONARY["dataset_sha256"],
            "pos_sha256": inputs.DICTIONARY["pos_sha256"],
        }
        with patch.object(recipe, "public_dataset_cli", return_value=evidence.json_bytes(observed)) as helper:
            self.assertEqual(observed, recipe.verify_embedded_dataset(self.root, self.root / "pkg.zip", 183))
            helper.assert_called_once_with(self.root, "--verify-package", self.root / "pkg.zip", 183)
            with self.assertRaisesRegex(inputs.InputError, "fixed PC dataset"):
                recipe.verify_embedded_dataset(self.root, self.root / "pkg.zip", 62)
            helper.return_value = evidence.json_bytes({**observed, "pos_sha256": "0" * 64})
            with self.assertRaisesRegex(inputs.InputError, "fixed PC dataset"):
                recipe.verify_embedded_dataset(self.root, self.root / "pkg.zip", 183)

    def test_ibus_build_rejects_package_changes_and_accepts_test_additions(self):
        original = (b'cc_binary(name="engine")\n'
                    b'mozc_cc_test(name="ibus_mozc_test", srcs=mozc_select(linux=["engine_test.cc"]), deps=[":engine"])\n')
        corrected = original.replace(b'"engine_test.cc"', b'"engine_test.cc", "property_handler_test.cc"')
        target = {"source_sha": "1" * 40, "changed_paths": [inputs.IBUS_BUILD]}

        def read_source(_root, _operation, spec):
            source = original if spec.startswith(inputs.BASE_SHA) else corrected
            return subprocess.CompletedProcess([], 0, stdout=source)

        with patch.object(inputs, "git", side_effect=read_source):
            inputs.verify_ibus_build(self.root, target)
            eligible = corrected
            corrected = eligible.replace(b'mozc_select(linux=', b'mozc_select(windows=')
            with self.assertRaisesRegex(inputs.InputError, "selector changed"):
                inputs.verify_ibus_build(self.root, target)
            corrected = eligible
            corrected += b'genrule(name="dictionary", cmd="", outs=["dict"])\n'
            with self.assertRaisesRegex(inputs.InputError, "confined to the pinned regression rule"):
                inputs.verify_ibus_build(self.root, target)

    def test_bazel_test_requires_a_result_and_ignores_cached_success(self):
        test = {"id": {"testResult": {"label": evidence.TEST_TARGET}}, "testResult": {"status": "PASSED"}}
        summary = {"id": {"testSummary": {"label": evidence.TEST_TARGET}},
                   "testSummary": {"overallStatus": "PASSED"}}

        def write_events(*events):
            return self.write("events.json", b"\n".join(json.dumps(item).encode() for item in events) + b"\n")

        build.require_executed_test(write_events(test, summary))
        with self.assertRaisesRegex(inputs.InputError, "uncached successful"):
            build.require_executed_test(write_events(summary))
        for cache in ({"cachedLocally": True}, {"executionInfo": {"cachedRemotely": True}}):
            cached = {**test, "testResult": {**test["testResult"], **cache}}
            with self.assertRaisesRegex(inputs.InputError, "uncached successful"):
                build.require_executed_test(write_events(cached, summary))

    def test_package_validation_preserves_assets_and_checks_each_elf(self):
        pc_zip, artifact = self.root / "pc.zip", self.root / "new.zip"

        def make_package(path, machine, icon, *, helper_memsz=120, program_offset=64, omit=None, xml=b"component"):
            with ZipFile(path, "w") as output:
                for binary in ("engine", "renderer", "server", "tool", "emacs-helper"):
                    header = b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack(
                        "<HHIQQQIHHHHHH", 2, machine, 1, 0, program_offset, 0, 0, 64, 56, 1, 0, 0, 0,
                    )
                    memory = helper_memsz if binary == "emacs-helper" else 120
                    output.writestr(f"bin/{binary}", header + struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 120, memory, 4096))
                for index in range(36):
                    output.writestr(f"icons/{index}.png", icon if index == 0 else b"icon")
                nested = io.BytesIO()
                with ZipFile(nested, "w") as icons:
                    icons.writestr("0.png", b"icon")
                for name, data in (("tmp/icons.zip", nested.getvalue()), ("usr/share/ibus/component/mozc.xml", xml),
                                   ("usr/share/emacs/site-lisp/emacs-mozc/mozc.el", b"emacs")):
                    if name != omit:
                        output.writestr(name, data)

        make_package(pc_zip, 62, b"same icon")
        make_package(artifact, 183, b"same icon")
        linkage = {"helper_linkage": "static", "interpreter": None, "needed": [], "closure": []}
        with patch.object(archive, "inspect_elf", return_value=linkage) as inspect:
            abi = archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            self.assertEqual(183, abi["elf_machine"])
            self.assertEqual(5, len(abi["elf_files_sha256"]))
            self.assertEqual(5, inspect.call_count)
            make_package(artifact, 183, b"tampered icon")
            with self.assertRaisesRegex(inputs.InputError, "non-ELF asset differs"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            make_package(artifact, 62, b"same icon")
            with self.assertRaisesRegex(inputs.InputError, "unexpected ELF identity"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            make_package(artifact, 183, b"same icon", omit="tmp/icons.zip")
            with self.assertRaisesRegex(inputs.InputError, "44-member"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            make_package(artifact, 183, b"same icon", xml=b"changed component")
            with self.assertRaisesRegex(inputs.InputError, "non-ELF asset differs"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            make_package(artifact, 183, b"same icon", helper_memsz=119)
            with self.assertRaisesRegex(inputs.InputError, "PT_LOAD memory size"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)
            make_package(artifact, 183, b"same icon", program_offset=4096)
            with self.assertRaisesRegex(inputs.InputError, "header table is outside"):
                archive.compare_packages(pc_zip, artifact, "aarch64-linux", self.root)

    def test_candidate_uses_exact_uploaded_bytes_and_keeps_upload_record_separate(self):
        local, metadata, _, job = self.outer_fixture()
        content = evidence.json_bytes(metadata)
        fetched = self.write("fetched.zip", local.read_bytes())
        digest = inputs.sha256_file(local)
        args = (local, fetched, "456", digest, job, metadata["target"]["source_sha"])
        with patch.dict(inputs.DICTIONARY, {"dataset_receipt_sha256": inputs.sha256_file(self.root / "dataset.json")}):
            candidate, upload = archive.candidate_records(*args)
            with self.assertRaisesRegex(inputs.InputError, "does not describe this corrected run"):
                archive.candidate_records(local, fetched, "456", digest, {**job, "ci_job_id": "999"}, args[-1])
        self.assertEqual(digest, candidate["archive_sha256"])
        self.assertEqual(hashlib.sha256(content).hexdigest(), candidate["metadata_sha256"])
        self.assertNotIn("ci_artifact_id", candidate)
        self.assertEqual("456", upload["ci_artifact_id"])
        self.assertEqual("https://api.github.com/repos/minamorl/mozkey/actions/artifacts/456/zip", candidate["archive_url"])
        fetched.write_bytes(fetched.read_bytes() + b"\n")
        with self.assertRaisesRegex(inputs.InputError, "Uploaded artifact bytes differ"):
            archive.candidate_records(*args)

    def test_preserves_notice_and_evidence_bytes_inside_the_archive(self):
        outer, metadata, stages, _ = self.outer_fixture()
        with ZipFile(outer) as output:
            self.assertEqual(archive.OUTER_MEMBERS, set(output.namelist()))
            self.assertEqual(len(output.namelist()), 17)
            self.assertEqual(b"documents/LICENSE", output.read("documents/LICENSE"))
            self.assertEqual(stages["tests"][1], output.read(metadata["tests"]["member_path"]))
            self.assertEqual(b"recipe/original-BUILD.bazel", output.read("recipe/original-BUILD.bazel"))

    def outer_fixture(self):
        package = self.write("mozc.zip", b"package fixture")
        receipt = self.write("dataset.json", b"{}\n")
        outer = self.root / "outer.zip"
        target = {"source_sha": "1" * 40, "tree_sha": "2" * 40, "files_sha256": dict.fromkeys(inputs.ALLOWED_PATHS, "1" * 64)}
        package_format = {"name": "ea2-ibus-corrected-mozc-v1", "source_sha": target["source_sha"],
                          "source_files_sha256": dict.fromkeys(inputs.FORMAT_PATHS, "1" * 64)}
        recipe_files = {name: name.encode() for name in recipe.RECIPE_MEMBERS}
        recipe_data = {"source_sha": "5" * 40, "tree_sha": "6" * 40, "repository": inputs.REPOSITORY,
                       "command": "bazel build //:package", "files_sha256": {"fixture": "7" * 64},
                       **{key: hashlib.sha256(recipe_files[name]).hexdigest() for name, key in recipe.RECIPE_MEMBERS.items()}}
        job = evidence.job_identity("x86_64-linux", "123", "456", recipe_data)
        notices = {name: ({"sha256": hashlib.sha256(name.encode()).hexdigest()}, name.encode()) for name in recipe.DOCUMENTS}
        commands = {
            "tests": ["bazel", "test", "--config=release_build", evidence.TEST_TARGET],
            "body_build": ["bazel", "build", "--config=release_build", "//:fixture"],
            "package_build": ["bazel", "build", "--config=release_build", "//:package"],
            "package_validation": ["python3", "-B", "corrected_build.py", "validate-package"],
        }
        stages = {}
        for kind, argv in commands.items():
            measurement = None if kind != "package_validation" else {
                "package_sha256": inputs.sha256_file(package),
                "notices": {name: record for name, (record, _) in notices.items()},
                "overlay": {key: recipe_data[key] for key in recipe.RECIPE_MEMBERS.values()},
                "embedded": {"elf_machine": 62, "dataset_sha256": inputs.DICTIONARY["dataset_sha256"],
                             "pos_sha256": inputs.DICTIONARY["pos_sha256"]},
                "abi": {
                    "system": "x86_64-linux", "elf_machine": 62,
                    "assets_sha256": dict.fromkeys(map(str, range(39)), "1" * 64),
                    "elf_files_sha256": dict.fromkeys(map(str, range(5)), "2" * 64),
                    "elf_measurements": dict.fromkeys(map(str, range(5)), {
                        "sha256": "2" * 64, "elf_machine": 62, "file_size": 120,
                        "pt_load": [{"offset": 0, "filesz": 120, "memsz": 124}],
                    }),
                },
            }
            stages[kind] = evidence.command_evidence(
                kind, target, subprocess.CompletedProcess(argv, 0), "3" * 64, "3" * 64, evidence.trace_id(), job, measurement,
            )
        with patch.dict(inputs.DICTIONARY, {"dataset_receipt_sha256": inputs.sha256_file(receipt)}):
            metadata = evidence.build_metadata("x86_64-linux", target, recipe_data, package_format, "123", "456", stages)
            archive.make_archive(outer, package, receipt, metadata, stages, notices, recipe_files)
        return outer, metadata, stages, job

    def test_failure_diagnostics_keep_command_exit_and_redacted_stderr(self):
        secret = "fixture-auth-value"
        result = subprocess.CompletedProcess(
            ["curl", f"https://user:{secret}@example.invalid/download?sig={secret}"], 37,
            stderr=(f"Authorization: Bearer {secret}\nretry https://example.invalid/a?sig={secret}\n"
                    "compiler: command failed at line 81\n").encode(),
        )
        failure = diagnostics.CommandFailure(result, (secret,)).details
        self.assertEqual(37, failure["exit_code"])
        self.assertEqual(["curl", "https://example.invalid/download"], shlex.split(failure["command"]))
        self.assertEqual("compiler: command failed at line 81", failure["stderr_tail"][-1])
        self.assertNotIn(secret, str(failure))

    def test_phase_logs_stdout_only_test_failure_and_keeps_success_output_quiet(self):
        command = ["bazelisk", "test", "--config=release_build", "--test_output=errors", evidence.TEST_TARGET]
        target = {"source_sha": "1" * 40, "tree_sha": "2" * 40}
        context = (self.root, target, self.root, self.root, {}, self.root, evidence.trace_id(), {})
        failed_test = b"property_handler_test.cc:81: Failure\nExpected event count: 2\n"
        for code in (4, 0):
            with self.subTest(exit_code=code):
                result = subprocess.CompletedProcess(command, code, stdout=failed_test,
                                                     stderr=b"" if code else b"noisy compiler output\n")
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(build, "arguments", return_value=SimpleNamespace(command="build")), \
                     patch.object(build, "run_build", side_effect=lambda *_: build.run_phase("tests", command, context)), \
                     patch.object(build, "worktree_state", return_value="3" * 64), \
                     patch.object(build, "verify_dataset"), patch.object(build, "verify_overlay"), \
                     patch.object(build, "run_command", return_value=result), \
                     redirect_stdout(stdout), redirect_stderr(stderr):
                    status = build.main()
                if code:
                    event = json.loads(stderr.getvalue())
                    details = event["details"]
                    self.assertEqual(1, status)
                    self.assertEqual("error", event["level"])
                    self.assertTrue(event["ts"].endswith("+00:00"))
                    self.assertEqual(code, details["exit_code"])
                    self.assertEqual("tests", details["kind"])
                    self.assertEqual(target["source_sha"], details["source_sha"])
                    self.assertEqual(command, shlex.split(details["command"]))
                    self.assertEqual(["property_handler_test.cc:81: Failure", "Expected event count: 2"],
                                     details["stdout_tail"])
                    self.assertEqual([], details["stderr_tail"])
                    self.assertEqual("", stdout.getvalue())
                else:
                    self.assertEqual(0, status)
                    self.assertEqual("", stderr.getvalue())
                    self.assertNotIn("property_handler_test.cc", stdout.getvalue())
                    self.assertNotIn("noisy compiler output", stdout.getvalue())

    def test_failure_tails_redact_and_bound_both_channels(self):
        secret = "fixture-private-value"
        result = subprocess.CompletedProcess(
            ["bazelisk", "test", "--token", secret], 1,
            stdout=("old stdout\n" * 20 + f"--token {secret}\n"
                    "property_handler_test.cc:81: Failure\n" + "x" * 600 + " end\n").encode(),
            stderr=("old stderr\n" * 9 + f"Authorization\x1b[0m: Bearer {secret}\n"
                    f"GET https://user:{secret}@example.invalid/a?key={secret}\n"
                    "compiler error: " + "y" * 600 + " end\n").encode(),
        )
        details = diagnostics.CommandFailure(result, (secret,), include_stdout=True).details
        self.assertEqual(16, len(details["stdout_tail"]))
        self.assertEqual(6, len(details["stderr_tail"]))
        for channel in ("stdout_tail", "stderr_tail"):
            self.assertEqual(480, len(details[channel][-1]))
            self.assertTrue(details[channel][-1].endswith(" end"))
            self.assertIn("[redacted credential-bearing line]", details[channel])
        self.assertIn("GET https://example.invalid/a", details["stderr_tail"])
        self.assertNotIn(secret, json.dumps(details))
        self.assertNotIn("stdout_tail", diagnostics.CommandFailure(result, (secret,)).details)

    def test_rejects_unsafe_zip_members(self):
        path = self.root / "unsafe.zip"
        with ZipFile(path, "w") as output:
            link = ZipInfo("ci/tests.json")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            output.writestr(link, "../metadata.json")
        with ZipFile(path) as output, self.assertRaisesRegex(inputs.InputError, "entry type"):
            archive.zip_members(output)
        with ZipFile(path, "w") as output:
            output.writestr("ci/../tests.json", b"fixture")
        with ZipFile(path) as output, self.assertRaisesRegex(inputs.InputError, "unsafe or duplicate"):
            archive.zip_members(output)
