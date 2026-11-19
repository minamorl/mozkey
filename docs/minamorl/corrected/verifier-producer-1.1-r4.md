# Corrected producer R4 lockfile verification

**PASS for the R4 code delta.** The frozen CI 1.1 and shared 0.8 SHA-256
values matched. The only producer edits in the R4 diff are lockfile flag
wiring in `corrected_build.py` and `corrected_recipe.py`. The source guard,
data inputs, package validation, workflow and provenance schema are unchanged
from the preceding checkout. The recipe command includes the added flag.

```text
$ git ls-tree -r 45682407ee02ce1efbdec0f3a425b31fd3d0bd5a -- src/MODULE.bazel src/MODULE.bazel.lock
exit 0
100644 blob e0b2d02f57b87e2c4516cb72cf36d42f19998aba	src/MODULE.bazel
$ git diff --check
exit 0, stdout empty

$ python3 -B tests/minamorl_corrected/check_frozen_source.py
exit 0
accepted source_sha: 45682407ee02ce1efbdec0f3a425b31fd3d0bd5a
tree_sha: 8d7172de14c73f01167029dc9e6623a2be0893b3
changed_paths: ["src/unix/ibus/BUILD.bazel", "src/unix/ibus/mozc_engine.cc", "src/unix/ibus/property_handler.cc", "src/unix/ibus/property_handler.h", "src/unix/ibus/property_handler_test.cc"]
target/format digest counts: 5 9
tracked checkout SHA-256: 61f03acd0e993df2f4ce767bf19c54c70b1463d01ff2826deefdbe541cf0cbf7
Untracked src/MODULE.bazel.lock rejected: Unexpected untracked path in the corrected source checkout
Tracked checkout SHA-256 after fixture removal: 61f03acd0e993df2f4ce767bf19c54c70b1463d01ff2826deefdbe541cf0cbf7

$ PYTHONPATH=tests/minamorl_corrected python3 -B -m unittest -v test_corrected_producer.ProducerTest.test_lockfile_mode_is_off_for_all_bazel_invocations_and_the_recipe
exit 0
test_lockfile_mode_is_off_for_all_bazel_invocations_and_the_recipe (test_corrected_producer.ProducerTest) ... ok

----------------------------------------------------------------------
Ran 1 test in 0.006s

OK

$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_R4_SCRATCH/r4_argv.py" "$VERIFIER_R4_SCRATCH"
exit 0
test: bazelisk test --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off --test_output=errors --nocache_test_results '--build_event_json_file=$RUNNER_TEMP/corrected-test-events.json' //unix/ibus:ibus_mozc_test
body query: bazelisk query '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --lockfile_mode=off --noimplicit_deps --output=label 'kind("cc_binary rule", deps(//unix:package))'
body build: bazelisk build --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off //unix/ibus:ibus_mozc
package: bazelisk build --config=release_build '--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%' --strip=never --lockfile_mode=off //:package
recipe: bazelisk build --config=release_build --package_path=$RUNNER_TEMP/dataset-overlay:%workspace% --strip=never --lockfile_mode=off //:package
package argv and recipe tokens match after overlay path substitution
```

The argv probe ran the real producer orchestration and spawned a stand-in
Bazelisk process to record the received arguments. It isolated recipe Git
measurement and non-Bazel work with fixtures. The only argv normalization
was the executable basename and replacing the scratch path with
`$RUNNER_TEMP`. Each of the four subprocesses received exactly one lockfile
flag, and the package arguments matched the recipe field. Bazel 9 documents
`update` as the default and `off` as skipping lockfile reads and writes.
[Bazel 9 command reference](https://bazel.build/versions/9.0.0/reference/command-line-reference).

The source gate separately used a real clean checkout, placed a synthetic
untracked lockfile under `src`, verified rejection and removed the fixture.
Its tracked fingerprint matched before and after.

Actual native CI and its resulting artifacts are the next gate. The verifier
did not dispatch CI, run a native Bazel build, operate the owner environment
or deliver Git changes. Earlier R3 checks outside this delta were not repeated.
