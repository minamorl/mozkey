"""Run the corrected producer on a native own-fork Actions runner."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import sys
from zipfile import BadZipFile

from corrected_archive import MACHINES, artifact_endpoint, candidate_records, compare_packages, make_archive
from corrected_diagnostics import CommandFailure, checked_output, emit_error, run_command
from corrected_evidence import TEST_TARGET, build_metadata, command_evidence, job_identity, json_bytes, trace_id
from corrected_inputs import (
    BASE_SHA, DICTIONARY, REPOSITORY, InputError, git, measure_target, require_commit,
    sha256_file, source_fingerprint, verify_dataset, verify_fork_checkout, verify_ibus_build, verify_pc_zip,
)
from corrected_recipe import (
    LOCKFILE_FLAG, PACKAGE_TARGET, json_object, measure_notices, measure_recipe, public_dataset_cli,
    recipe_members, release_build_options, verify_embedded_dataset, verify_overlay,
)


HOSTS = {"x86_64-linux": "x86_64", "aarch64-linux": "aarch64"}


def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def worktree_state(target_dir, sha):
    allowed_links = {"src/bazel-bin", "src/bazel-out", "src/bazel-src", "src/bazel-testlogs"}
    for raw in git(target_dir, "ls-files", "--others", "-z").stdout.split(b"\0")[:-1]:
        name = os.fsdecode(raw)
        if name not in allowed_links or not (target_dir / name).is_symlink():
            raise InputError("Unexpected untracked path in the corrected source checkout")
    return source_fingerprint(target_dir, sha)


def body_targets(bazel, workspace, registration):
    expression = 'kind("cc_binary rule", deps(//unix:package))'
    raw = checked_output([bazel, "query", registration, LOCKFILE_FLAG, "--noimplicit_deps", "--output=label", expression],
                      cwd=workspace)
    labels = sorted(set(line for line in raw.decode("utf-8").splitlines() if line.startswith("//")))
    if not labels or any(not re.fullmatch(r"//[A-Za-z0-9_/.-]*:[A-Za-z0-9_/.-]+", label) for label in labels):
        raise InputError("Could not identify the corrected package's workspace binary targets")
    return labels


def require_executed_test(events):
    results = []
    summaries = []
    with events.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            event_id = event.get("id", {})
            if event_id.get("testResult", {}).get("label") == TEST_TARGET:
                results.append(event["testResult"])
            if event_id.get("testSummary", {}).get("label") == TEST_TARGET:
                summaries.append(event["testSummary"])
    if not results or not all(
        item.get("status") == "PASSED" and not item.get("cachedLocally", False)
        and not item.get("executionInfo", {}).get("cachedRemotely", False)
        for item in results
    ) or len(summaries) != 1 or summaries[0].get("overallStatus") != "PASSED":
        raise InputError("Bazel did not report an uncached successful IBus regression test")


def run_phase(kind, argv, context, report=None):
    target_dir, target, dataset, overlay, overlay_state, ea2, trace, job = context
    before = worktree_state(target_dir, target["source_sha"])
    verify_dataset(dataset)
    verify_overlay(ea2, target_dir, overlay, overlay_state)
    result = run_command(argv, cwd=target_dir / "src")
    include_stdout = kind in {"tests", "body_build", "package_build"}
    identity = {"kind": kind, "source_sha": target["source_sha"], "tree_sha": target["tree_sha"]}
    # Retain the command and exit code even when the post-command integrity check fails.
    try:
        after = worktree_state(target_dir, target["source_sha"])
        verify_dataset(dataset)
        verify_overlay(ea2, target_dir, overlay, overlay_state)
    except (OSError, ValueError) as error:
        emit_error(trace, "CI_INTEGRITY_FAILURE", "Post-command input verification failed",
                   {**identity, **CommandFailure(result, include_stdout=include_stdout).details})
        raise InputError("Post-command input verification failed") from error
    if result.returncode:
        failure = CommandFailure(result, include_stdout=include_stdout)
        failure.details.update(identity)
        raise failure
    if before != after:
        raise InputError(f"Corrected CI stage {kind} failed")
    measurement = {**json_object(result.stdout), **report} if report is not None else None
    record, content = command_evidence(kind, target, result, before, after, trace, job, measurement)
    event = json_object(content)
    emit({key: event[key] for key in ("ts", "level", "trace_id", "msg", "kind", "command", "exit_code")})
    return record, content


def run_build(args, trace):
    if (platform.system() != "Linux" or platform.machine() != HOSTS[args.system]
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_SHA") != args.control_sha
            or os.environ.get("GITHUB_RUN_ID") != args.run_id
            or not re.fullmatch(r"[0-9]+", args.job_id)):
        raise InputError("The build needs a native Linux workflow_dispatch runner in the own fork")
    target_dir, control_dir = args.target.resolve(strict=True), args.control.resolve(strict=True)
    if target_dir == control_dir or target_dir in control_dir.parents or control_dir in target_dir.parents:
        raise InputError("Target and recipe require separate checkouts")
    verify_fork_checkout(target_dir, args.source_sha)
    target, package_format = measure_target(target_dir, args.source_sha)
    verify_ibus_build(target_dir, target)
    worktree_state(target_dir, args.source_sha)
    recipe = measure_recipe(control_dir, args.control_sha, args.bazel)
    root = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    ea2 = root / "mozkey-ea2"
    git(target_dir, "worktree", "add", "--quiet", "--detach", str(ea2), BASE_SHA)
    try:
        build_outputs(args, trace, root, ea2, target_dir, control_dir, target, package_format, recipe)
    finally:
        git(target_dir, "worktree", "remove", str(ea2))


def build_outputs(args, trace, root, ea2, target_dir, control_dir, target, package_format, recipe):
    pc_zip = root / "mozkey-ea2-pc.zip"
    dataset = root / "mozkey-pc-dataset"
    overlay = root / "dataset-overlay"
    results = root / "mozkey-corrected"
    results.mkdir()
    checked_output(["curl", "--fail", "--silent", "--show-error", "--location",
                 "--proto", "=https", "--retry", "0", "--output", str(pc_zip), DICTIONARY["pc_zip_url"]])
    verify_pc_zip(pc_zip)
    public_dataset_cli(control_dir, pc_zip, dataset)
    verify_dataset(dataset)
    notices = measure_notices(control_dir, ea2, dataset, root / "mozkey-notice-snapshot")
    overlay_state = json_object(public_dataset_cli(control_dir, "--overlay", ea2, dataset, overlay))
    digests = verify_overlay(ea2, target_dir, overlay, overlay_state)

    registration = f"--package_path={overlay}:%workspace%"
    build_options = release_build_options(registration)
    workspace = target_dir / "src"
    events = root / "corrected-test-events.json"
    job = job_identity(args.system, args.run_id, args.job_id, recipe)
    context = (target_dir, target, dataset, overlay, overlay_state, ea2, trace, job)
    tests = run_phase("tests", [args.bazel, "test", *build_options, "--test_output=errors", "--nocache_test_results",
                                f"--build_event_json_file={events}", TEST_TARGET], context)
    require_executed_test(events)
    body = run_phase("body_build", [args.bazel, "build", *build_options,
                                    *body_targets(args.bazel, workspace, registration)], context)
    package = run_phase("package_build", [args.bazel, "build", *build_options, PACKAGE_TARGET], context)

    package_path = workspace / "bazel-bin" / "unix" / "mozc.zip"
    if recipe != measure_recipe(control_dir, args.control_sha, args.bazel):
        raise InputError("The build recipe changed during CI")
    validation = run_phase("package_validation", [
        sys.executable, "-B", str(control_dir / "tools/minamorl/corrected_build.py"), "validate-package",
        "--control", str(control_dir), "--pc-zip", str(pc_zip), "--package", str(package_path),
        "--system", args.system,
    ], context, {"overlay": overlay_state, "notices": {name: record for name, (record, _) in notices.items()}})
    evidence = {"tests": tests, "body_build": body, "package_build": package, "package_validation": validation}
    metadata = build_metadata(args.system, target, {**recipe, **digests}, package_format,
                              args.run_id, args.job_id, evidence)
    archive = results / f"mozkey-corrected-{args.system}.zip"
    make_archive(archive, package_path, dataset / "dataset.json", metadata, evidence,
                 notices, recipe_members(ea2, overlay, digests))


def validate_package(args):
    verify_pc_zip(args.pc_zip)
    return {
        "package_sha256": sha256_file(args.package),
        "embedded": verify_embedded_dataset(args.control, args.package, MACHINES[args.system]),
        "abi": compare_packages(args.pc_zip, args.package, args.system, Path(os.environ["RUNNER_TEMP"])),
    }


def publish_records(args):
    endpoint = artifact_endpoint(args.artifact_id)
    auth = os.environ.get("GH_TOKEN")
    if not auth:
        raise InputError("Actions ZIP transport requires the workflow token")
    with args.downloaded.open("xb") as download:
        checked_output(["gh", "api", endpoint], stdout=download, auth=auth)
    job = {"ci_run_id": args.run_id, "ci_job_id": args.job_id, "system": args.system,
           "recipe_source_sha": args.control_sha}
    candidate, upload = candidate_records(args.archive, args.downloaded, args.artifact_id,
                                          args.artifact_digest, job, args.source_sha)
    args.output.mkdir()
    (args.output / "candidate.json").write_bytes(json_bytes(candidate))
    (args.output / "upload.json").write_bytes(json_bytes(upload))


def resolve_job(name):
    auth = os.environ.get("GH_TOKEN")
    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if (not auth or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (run, attempt))):
        raise InputError("The native Actions job coordinates are unavailable")
    endpoint = f"repos/{REPOSITORY}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    response = json_object(checked_output(["gh", "api", endpoint], auth=auth))
    matches = [job["id"] for job in response["jobs"] if job["name"] == name and job["status"] == "in_progress"]
    if len(matches) != 1 or type(matches[0]) is not int or matches[0] <= 0:
        raise InputError("Expected one matching active native Actions job")
    print(matches[0])


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for name in ("target", "control"):
        build.add_argument(f"--{name}", type=Path, required=True)
    for name in ("source-sha", "control-sha"):
        build.add_argument(f"--{name}", type=require_commit, required=True)
    for name in ("run-id", "job-id", "bazel"):
        build.add_argument(f"--{name}", required=True)
    build.add_argument("--system", choices=HOSTS, required=True)
    publish = commands.add_parser("publish-records")
    for name in ("archive", "downloaded", "output"):
        publish.add_argument(f"--{name}", type=Path, required=True)
    for name in ("run-id", "job-id", "artifact-id", "artifact-digest"):
        publish.add_argument(f"--{name}", required=True)
    for name in ("source-sha", "control-sha"):
        publish.add_argument(f"--{name}", type=require_commit, required=True)
    publish.add_argument("--system", choices=HOSTS, required=True)
    validation = commands.add_parser("validate-package")
    for name in ("control", "pc-zip", "package"):
        validation.add_argument(f"--{name}", type=Path, required=True)
    validation.add_argument("--system", choices=HOSTS, required=True)
    commands.add_parser("resolve-job").add_argument("--name", required=True)
    return parser.parse_args()


def main():
    args = arguments()
    trace = trace_id()
    try:
        if args.command == "build":
            run_build(args, trace)
        elif args.command == "publish-records":
            publish_records(args)
        elif args.command == "validate-package":
            emit(validate_package(args))
        else:
            resolve_job(args.name)
    except (OSError, ValueError, KeyError, BadZipFile, SyntaxError) as error:
        details = {"stage": args.command}
        if isinstance(error, CommandFailure):
            details.update(error.details)
        message = str(error) if isinstance(error, (InputError, CommandFailure)) else "Producer input or output validation failed"
        emit_error(trace, "CORRECTED_PRODUCER_FAILURE", message, details)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
