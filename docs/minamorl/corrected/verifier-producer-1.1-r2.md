# Corrected producer R2 pre-dispatch verification

**Verdict RED on failure diagnostics.** The actual corrected source now
passes. The two real baseline packages pass the complete asset and ELF checks.
The producer drops failed Bazel test details delivered through stdout.

## Fixed inputs

```text
$ shasum -a 256 pins/domains/mozkey-corrected-ci.spec pins/domains/mozkey-fork-distribution.spec pins/house_style.pin pins/prohibitions.pin
exit 0
dd9b706c18adfbcd228a3e9a67a3af6f0a1f202a2a4472198616b17213f39b03  pins/domains/mozkey-corrected-ci.spec
ad4d0904080301ad853b8ca2e39016820cea0d6fad54a45238229742e92892d2  pins/domains/mozkey-fork-distribution.spec
1647beca51ca87eac45aed84fa3a81249664789beceba79624cfdbbbb08c0e89  pins/house_style.pin
1d6e845bc1ed834fb41ba4d285d3be7ece8d2e31898f51e23bf58ee7ff995be8  pins/prohibitions.pin

$ git diff --exit-code 2221a65432eda9c71c79ff08e8008dc218720fab --
exit 0, stdout empty

$ shasum -a 256 /Users/minamorl/.cc/tmp/Kaq9O03T/mozkey-arm.Ib9l4169/artifact-14510161453.zip
exit 0
f417991e62c5a7361c01b4cd07fc10d8b75a02371bb05392a6f124efea0ce738  /Users/minamorl/.cc/tmp/Kaq9O03T/mozkey-arm.Ib9l4169/artifact-14510161453.zip
```

## Local gates

```text
$ python3 -B -m unittest discover -s tests/minamorl_corrected -v
exit 0
----------------------------------------------------------------------
Ran 21 tests in 1.509s

OK

$ python3 -B tests/minamorl_corrected/check_frozen_source.py
exit 0
accepted source_sha: 3ae0bfb224dfa6c5cb6dfc125ca5dd8db444f799
tree_sha: 4fe19196cb1c61e9b03849101623ef3cc6cb0dd4
changed_paths: ["src/unix/ibus/BUILD.bazel", "src/unix/ibus/mozc_engine.cc", "src/unix/ibus/property_handler.cc", "src/unix/ibus/property_handler.h", "src/unix/ibus/property_handler_test.cc"]
target/format digest counts: 5 9
tracked checkout SHA-256: 78e3e81c8c4d1bf325d011916613af44d7bcc3df7ffd2c220048200c592b4bdb

$ python3 -B tests/minamorl_corrected/check_fixed_pc.py
exit 0
EA2 tracked checkout fingerprint: e6e413b01071ccf3e9c6c432e2246da9400e5f89d7bed8c2614275f72708a22f
PC archive, dataset, POS and receipt SHA-256 verified
PC embedded dataset and POS verified for ELF machine: 62
overlay public receipt verified: encoder_sha256, original_build_sha256, overlay_build_sha256
PC package regular/ELF/non-ELF member counts: 44 5 39
PC ELF PT_LOAD counts: [2, 2, 2, 2, 2]
Designated recipe members verified: recipe/embed_file.py, recipe/original-BUILD.bazel, recipe/overlay-BUILD.bazel
EA2 snapshot notices verified: 7

$ npx --yes prettier@3.6.2 --no-config --check .github/workflows/minamorl-linux-corrected.yml
exit 0
Checking formatting...
All matched files use Prettier code style!

$ ruby "$VERIFIER_SCRATCH/workflow_check.rb" .github/workflows/minamorl-linux-corrected.yml
exit 0
x86_64-linux ubuntu-24.04 Corrected x86_64-linux
aarch64-linux ubuntu-24.04-arm Corrected aarch64-linux
bash -n passed for 6 workflow scripts
```

Source checkout and fetched refs were in disposable scratch. The verifier
used Git object paths to sample the immutable target and did not read the
owner's working C++ files. Tests and PC gate logs contained no `FAIL`,
`ERROR` or `Traceback` lines.

## Independent probe

```text
$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_R2_SCRATCH/r2_probe.py" "$VERIFIER_R2_SCRATCH" "$FIXED_PC_ZIP" "$CACHED_ARM_ACTIONS_ZIP"
exit 0
actual package 62 members 44 non-ELF 39
ELF 62 usr/bin/mozc_emacs_helper PT_LOAD 2
ELF 62 usr/lib/ibus-mozc/ibus-engine-mozc PT_LOAD 2
ELF 62 usr/lib/mozc/mozc_renderer PT_LOAD 2
ELF 62 usr/lib/mozc/mozc_server PT_LOAD 2
ELF 62 usr/lib/mozc/mozc_tool PT_LOAD 2
actual package 183 members 44 non-ELF 39
ELF 183 usr/bin/mozc_emacs_helper PT_LOAD 2
ELF 183 usr/lib/ibus-mozc/ibus-engine-mozc PT_LOAD 2
ELF 183 usr/lib/mozc/mozc_renderer PT_LOAD 2
ELF 183 usr/lib/mozc/mozc_server PT_LOAD 2
ELF 183 usr/lib/mozc/mozc_tool PT_LOAD 2
nested icons: raw_equal False contents_equal True inner_files 26
equal-count XML replacement rejected: The package does not retain the complete 44-member EA2 inventory
ARM PT_LOAD p_memsz < p_filesz rejected: ELF PT_LOAD memory size is smaller than file size
ARM PT_LOAD out-of-file offset rejected: ELF program segment is outside the file
recipe recipe/embed_file.py 0d7fe3d6616f8dc742f8f9c0559a5ac8cdeb5c8ef33577df52d09cbed0996b28
recipe recipe/original-BUILD.bazel 115db49f3f2ed7fd947bf5ee26df4b281c63ccb1e5b36b800cb248dddd31bc6e
recipe recipe/overlay-BUILD.bazel f5cbbd3ace07d00d534fa32698ba35143aa993a21f54ef81b9af178e3053c324
fixture outer: 17 members, 7 documents, 3 recipe members
fixture credential-free URL: https://api.github.com/repos/minamorl/mozkey/actions/artifacts/14510161453/zip
same run, different job rejected: Uploaded metadata does not describe this corrected run
same job, different recipe rejected: Uploaded metadata does not describe this corrected run
child rc: 17 stdout captured: True stdout emitted: False stderr lines: 6 last stderr line: [redacted credential-bearing line]
```

The two package byte streams were real. The ARM inner ZIP was read from
the outer artifact after the outer hash check. The public helper validated
the embedded dataset and POS independently for machines 62 and 183. The
26 nested icon paths and bytes matched across the two architectures. The
probe compared the designated EA2 original BUILD and encoder against
their exact Git blobs, and compared the generated overlay member against
the actual overlay file. The R2 gate requires the IBus XML and Emacs client
as part of all 44 regular files.

The proof of output shape used the initial ARM package, real PC receipt,
generated recipe bytes and seven real documents with simulated CI statuses and
fixture run/job IDs. Each of the four evidence
member hashes matched the ZIP. Metadata contains no post-upload ID, outer ZIP
hash or final manifest hash. The fixtures and checked code bind target SHA,
tree, dataset, matching before/after source hashes, system, run/job IDs and
separate recipe source/tree. The shell passes the same recipe SHA and job ID at
build and publication. The publication step names the artifact returned by
the same upload step, downloads through authenticated `gh api`, and requires
the assembled, fetched and upload-reported ZIP hashes to match. Actions
download transport may require authentication. A final public release asset
is outside this gate. [GitHub artifact download API](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact).

## Remaining failure

The test command at `tools/minamorl/corrected_build.py:137–145` invokes
`bazelisk test` with `--config=release_build`, `--test_output=errors`,
`--nocache_test_results` and the fixed IBus test target. The following
commands build the body and the canonical `//:package` with that release
profile. The target commit pins Bazel 9.0.2. Its `src/.bazelrc:33–34` adds
`--compilation_mode=opt` and `--copt=-DABSL_MIN_LOG_LEVEL=100`. These options
configure the build inherited by `test`. The build-event guard requires a
successful uncached test result and the matching summary.
[Bazel rc inheritance](https://bazel.build/versions/9.0.0/run/bazelrc).
[Bazel test options](https://bazel.build/versions/9.0.0/reference/command-line-reference).

The expected failure diagnostics preserve useful bounded redacted stdout and
stderr. Bazel 9.0.2's `TestStrategy.processTestOutput` sends the failed
test log to `getOutputStream()`. The producer captures stdout in
`corrected_diagnostics.py:49–54`. `CommandFailure` at lines 38–46 builds
the emitted details from command, exit code and the last six redacted stderr
lines. `corrected_build.py:85–86,245–250` emits those details and drops
stdout. The child-process probe confirmed that stdout arrived and was absent
from the emitted fields. It also verified that an unlisted environment
variable was withheld and an authorization line was redacted. To close
the gap, send a bounded redacted stdout tail for captured Bazel failures
or preserve the selected failed test log with equivalent bounds and redaction.
Binary download stdout must stay out of diagnostics.
[Bazel 9.0.2 test-log output path](https://github.com/bazelbuild/bazel/blob/9.0.2/src/main/java/com/google/devtools/build/lib/analysis/test/TestStrategy.java#L365-L379).

## Unverified

Real corrected CI/job records, corrected Linux linkage, tests, body/package,
post-upload corrected ZIP download and the future source SHA remain pending.
The recipe working files are untracked, so the fixed recipe commit is also
pending. No native build, workflow dispatch, owner operation or Git
delivery was performed.
