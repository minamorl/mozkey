# Corrected producer R5/R6 pre-dispatch verification

**RED on malformed cquery output.** The configured graph command, five
expected binary labels, required test and package arguments, and APT
fixtures passed. A recursive Bazel build pattern crossed the label guard.

## Measured

The reviewed R5/R6 diff is relative to
`e6581f401223e71058a20c0e5dd865bcc1254a9b`. The source guard, diagnostics,
archive format, package and evidence verification files showed no diff.
The two frozen spec hashes matched CI 1.1 and parent 0.8.

```text
$ git diff --check
exit 0, stdout empty
$ PYTHONPATH=tests/minamorl_corrected python3 -B -m unittest -v test_corrected_apt test_corrected_producer.ProducerTest.test_configured_cquery_and_all_bazel_invocations_share_the_release_options test_corrected_producer.ProducerTest.test_configured_cquery_rejects_empty_or_malformed_output
exit 0
test_deb822_preserves_signed_by_components_and_architectures (test_corrected_apt.AptSetupTest) ... ok
test_legacy_changes_only_matching_active_ubuntu_amd64_uris (test_corrected_apt.AptSetupTest) ... ok
test_workflow_passes_bounded_options_to_update_and_install (test_corrected_apt.AptSetupTest) ... ok
test_configured_cquery_and_all_bazel_invocations_share_the_release_options (test_corrected_producer.ProducerTest) ... ok
test_configured_cquery_rejects_empty_or_malformed_output (test_corrected_producer.ProducerTest) ... ok

----------------------------------------------------------------------
Ran 5 tests in 0.008s

OK

$ ruby "$VERIFIER_SCRATCH/workflow_check.rb" .github/workflows/minamorl-linux-corrected.yml
exit 0
x86_64-linux ubuntu-24.04 Corrected x86_64-linux
aarch64-linux ubuntu-24.04-arm Corrected aarch64-linux
bash -n passed for 6 workflow scripts

$ npx --yes prettier@3.6.2 --no-config --check .github/workflows/minamorl-linux-corrected.yml
exit 0
Checking formatting...
All matched files use Prettier code style!

$ python3 -B tools/minamorl/corrected_apt.py
exit 1, expected scope rejection
code=APT_RUNNER_SCOPE
msg=Expected the own-fork amd64 Actions runner

$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_R6_SCRATCH/probe.py" "$VERIFIER_R6_SCRATCH"
exit 1
bazelisk test --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off --test_output=errors --nocache_test_results '--build_event_json_file=$RUNNER_TEMP/corrected-test-events.json' //unix/ibus:ibus_mozc_test
bazelisk cquery --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off --universe_scope=//:package --noimplicit_deps --output=starlark '--starlark:expr=str(target.label)' 'filter("^(@@)?//", kind("^cc_binary rule$", deps(//:package)))'
bazelisk build --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off //gui/tool:mozc_tool //renderer/qt:mozc_renderer //server:mozc_server //unix/emacs:mozc_emacs_helper //unix/ibus:ibus_mozc
bazelisk build --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off //:package
cquery output rejected: b''
cquery output rejected: b'@@outside//foo:bar\n'
cquery output rejected: b'//unix/../../ibus:engine\n'
cquery output rejected: b'//unix:engine (abcd)\n'
cquery output rejected: b'//unix:engine\n--not-a-label\n'
cquery recursive build pattern accepted: ['//unix/...:all']
APT fixtures: arm64 bytes unchanged; amd64 official HTTPS only; inline key, disabled stanza, ports, vendor and arch filters preserved
recipe member tools/minamorl/corrected_apt.py 91706538e0587b01b0fefe98d2ac728884d52641b17a619cf80002e28bd3407a
```

The probe spawned a stand-in Bazelisk through the real orchestration.
Other build phases and input validations were isolated with fixtures. The
argv snippet substitutes only the scratch path with `$RUNNER_TEMP`. The
same Linux release options, overlay, strip and lockfile settings are passed
to the test, configured query and both builds. Starlark emits the label
without a configuration suffix. The Python parser normalizes and
deduplicates bare and canonical main-repository labels before body build.
The actual target's `src/.bazelrc` selects `oss_linux` on Linux, and the
`src/bazel/qt.bzl` references to `@qt_win` are in Windows selections. The
actual target's Linux package genrule consumes the same five executable
targets used in the positive fixture. Bazel documents that cquery inherits
build settings and follows the active select branch.
[Configured query](https://bazel.build/versions/9.0.0/query/cquery).
[Bazel rc inheritance](https://bazel.build/versions/9.0.0/run/bazelrc).

The APT probe touched fixture files in scratch. It exercised a regional EC2
Deb822 mirror with separate archive and security suites, inline signing key
continuations, a disabled stanza, ARM ports and a lookalike vendor host,
and legacy architecture additions/removals. It checked exact preservation
outside the expected official mirror changes and preservation of the
applicable suite set, signing keys, architecture and component fields.
Workflow lines 81–93 bound the step to eight minutes, set the HTTP and
HTTPS acquisition timeouts to ten seconds and set two retries on both APT
calls. The helper is invoked in the x86_64 job only. The CLI additionally
checks Linux amd64, the own fork and GitHub Actions. No trust-disabling
options or config dumps appeared in the delta. `corrected_apt.py` is included
in `corrected_recipe.py:21–32` for Git blob and checkout-byte measurement.
[APT source formats](https://manpages.debian.org/unstable/apt/sources.list.5.en.html).
[APT acquisition options](https://manpages.debian.org/unstable/apt/apt.conf.5.en.html).

## Gap

`tools/minamorl/corrected_build.py:27,48–54` rejects `.` and `..` segments,
which leaves the dot-only package segment `...` accepted. A child emitted
`@@//unix/...:all`, and `body_targets` returned `['//unix/...:all']`. The
caller expands the result as build arguments at lines 154–159. Bazel rejects
dot-only path components in package names and interprets this exact shape
as a recursive target pattern. The injected line was synthetic and no such
output from native Bazel was observed. Reject dot-only package components
and ensure wildcard patterns cannot enter the body-build command.
[Bazel 9.0.2 label validator](https://github.com/bazelbuild/bazel/blob/9.0.2/src/main/java/com/google/devtools/build/lib/cmdline/LabelValidator.java#L75-L125).
[Bazel target patterns](https://bazel.build/versions/9.0.0/run/build#building-multiple-targets).

## Unverified

Real configured Bazel execution, APT acquisition on the hosted runners,
the fixed R5/R6 recipe commit and CI3 artifacts remain next-gate evidence.
This verifier did not execute a native build, write to system APT files,
write to Git or dispatch CI.
