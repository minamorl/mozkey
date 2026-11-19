# Corrected producer 1.0 pre-dispatch verification

**Verdict RED.** The actual public-fork source passes the Git identity, path and
digest checks. The BUILD guard then rejects its checked-in regression rule
before either runner can invoke Bazel.

## Inputs and unchanged baseline

```text
$ shasum -a 256 pins/domains/mozkey-corrected-ci.spec pins/domains/mozkey-fork-distribution.spec pins/house_style.pin pins/prohibitions.pin
exit 0
51ed21aec63714548a6816b126c12d82449dd6bdf244f8d5def521403ccbb3d5  pins/domains/mozkey-corrected-ci.spec
3379db19a0ec1b10f52173e0806887d629d38a3109dc1885473a89568d56881a  pins/domains/mozkey-fork-distribution.spec
1647beca51ca87eac45aed84fa3a81249664789beceba79624cfdbbbb08c0e89  pins/house_style.pin
1d6e845bc1ed834fb41ba4d285d3be7ece8d2e31898f51e23bf58ee7ff995be8  pins/prohibitions.pin

$ git rev-parse HEAD
exit 0
2221a65432eda9c71c79ff08e8008dc218720fab
$ git diff --exit-code 2221a65432eda9c71c79ff08e8008dc218720fab --
exit 0, stdout empty

$ git merge-base --is-ancestor ea2e9f5cfb80fccf50caca1af097b2f0830be52a 3ae0bfb224dfa6c5cb6dfc125ca5dd8db444f799
exit 0, stdout empty
$ git ls-remote --heads --tags https://github.com/minamorl/mozkey
exit 0, matching line
3ae0bfb224dfa6c5cb6dfc125ca5dd8db444f799	refs/heads/fix/ibus-property-output-ea2-20261119
```

The public ref was checked separately from the existing source checkout. That
checkout's local origin fails the producer's exact HTTPS origin guard. The
workflow requests the public HTTPS origin through its explicit repository input.
The recorded target tree is `4fe19196cb1c61e9b03849101623ef3cc6cb0dd4`.

```text
$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_SCRATCH/source_check.py"
exit 1
source 3ae0bfb224dfa6c5cb6dfc125ca5dd8db444f799 tree 4fe19196cb1c61e9b03849101623ef3cc6cb0dd4 base ea2e9f5cfb80fccf50caca1af097b2f0830be52a
changed paths: src/unix/ibus/BUILD.bazel src/unix/ibus/mozc_engine.cc src/unix/ibus/property_handler.cc src/unix/ibus/property_handler.h src/unix/ibus/property_handler_test.cc
59acd943a0d15254345f3e176f42786af2b4fba83b1657341cf56e017a7db19a src/.bazeliskrc
08ac59def69d2100356fcce7f74a32775bd4c142378689338182f7b5e48999af src/config.bzl
a0cc21085ed84db13fffdb95e7ce4be88395f71293946feeab95b8fabddc3b1a src/data/dictionary_koyasi/BUILD.bazel
dfbe4196c50c956c7abc33c902a80f0babaaad347d393c25c608a9f8cec3956a src/data/dictionary_oss/BUILD.bazel
5abdaa43194aa97593e437dd54a59c1ff3e1937894e980ca3439439f64a65962 src/unix/BUILD.bazel
c0a349680b64aafa4f1b08d296dfb16b4110e52384ce7f35334c9850bb536a91 src/unix/build_icons.py
fa05a3838125a479375e6d11d72bbf8767d916b7f05e2ae1ca57ad8372aaaa19 src/unix/build_package.py
5d088969951fe84b799911eae80ead50a6d51a027ffbebd2a64be857a00530d6 src/unix/ibus/BUILD.bazel
e8fabab2bc690830b26b7cab28e0a6fefd3c5ef6ad05fd6714088158d9ff2138 src/unix/ibus/gen_mozc_xml.py
worktree_state 78e3e81c8c4d1bf325d011916613af44d7bcc3df7ffd2c220048200c592b4bdb
local existing checkout fork guard: The source was not checked out from the own fork
verify_ibus_build: rejected: Changed regression attributes must be literal string lists
```

Each digest above was obtained separately from `git show SHA:path` and compared
with the producer map. The eight paths besides the IBus BUILD matched EA2 bytes.

## Unmet checks and changes needed

1. **Dispatch blocker** — Corrected CI 1.0 lines 16 and 28–31 and shared 0.7
   lines 171–174 require the eligible source to reach its IBus regression test.
   The actual target's `src/unix/ibus/BUILD.bazel:315` adds
   `property_handler_test.cc` inside the existing
   `mozc_select(linux = [...])` and appends three workspace deps. The producer
   calls `string_list` on the `ast.Call` at
   `tools/minamorl/corrected_inputs.py:195–205`. The shipped positive test
   uses a literal list at `tests/minamorl_corrected/test_corrected_producer.py:77–90`.
   Accept the existing selector shape while preserving the call and selector
   keys and all existing ordered entries. Allow only the specified regression
   source and approved deps to be added. Recheck the real SHA and reject
   changes outside that rule, selector edits and removed or reordered entries.
   The guard is called at `tools/minamorl/corrected_build.py:124–126`.

2. **Package inventory** — `tools/minamorl/corrected_archive.py:95–117`
   compares five ELF paths and notice/icon paths and bytes. The fixed PC package
   has 44 regular members. The independent negative fixture retained its real
   five ELF bytes and all 36 icon bytes, removed `tmp/icons.zip`, the IBus
   component XML and `mozc.el`, then added an extra file. The comparison
   accepted it. Only the native ELF linkage resolver was mocked. Compare the
   complete expected regular-member inventory, including the IBus XML and
   Emacs Lisp client. Retain the existing byte and ELF verification and check
   component identity and retained assets where format allows.

3. **Failed CI diagnosis** — `tools/minamorl/corrected_build.py:33–45,86–108`
   passes an environment allowlist to Bazel and captures stdout while
   discarding stderr with `DEVNULL`. The stage emits command, exit code and
   input measurements. It emits no compiler or test diagnostic, and the
   workflow contains no failure upload for test logs. The controlled child
   returned 17 and its stderr was already gone at the subprocess boundary.
   Keep the allowlist, capture bounded redacted stderr on failure, explicitly
   request failed test output or read the selected test log, and include an
   actionable phase-tagged diagnostic. Opaque helpers and ELF inspection also
   discard stderr at `corrected_recipe.py:38–43` and
   `corrected_archive.py:61–66`. Ensure run context exposed to those
   subprocesses is limited to their required variables.

4. **Public candidate download** — `corrected_archive.py:175–202` fixes
   `archive_url` to the v7 Actions UI URL. The workflow separately downloads
   through the authenticated artifact ZIP REST endpoint at
   `.github/workflows/minamorl-linux-corrected.yml:117–134`. GitHub documents
   that the UI route requires sign-in. An existing artifact from the same
   public fork was checked without credentials and returned no ZIP on either
   route. This is a public handoff gap. For that handoff, use a stable
   anonymously downloadable URL for the same measured bytes and verify the
   exact emitted URL and SHA-256 together. A signed-in download and API
   authentication are different transport conditions. No corrected upload
   exists from this verification.
   [GitHub upload-artifact v7 outputs](https://github.com/actions/upload-artifact/tree/v7#outputs).
   [Download an artifact API](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact).

## Local checks

```text
$ python3 -B -m unittest discover -s tests/minamorl_corrected -v
exit 0
----------------------------------------------------------------------
Ran 19 tests in 2.460s

OK

$ python3 -B tests/minamorl_corrected/check_fixed_pc.py
exit 0
EA2 tracked checkout fingerprint: e6e413b01071ccf3e9c6c432e2246da9400e5f89d7bed8c2614275f72708a22f
PC archive, dataset, POS and receipt SHA-256 verified
PC embedded dataset and POS verified for ELF machine: 62
overlay public receipt verified: encoder_sha256, original_build_sha256, overlay_build_sha256
PC package ELF count: 5
PC top-level package notice/icon inventory: 0 36
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

$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_SCRATCH/independent_checks.py" fixture "$VERIFIER_SCRATCH"
exit 0
run_quiet failure returncode = 17 stderr = None stdout = b'public-stdout\n'
fixed input fixed-pc.zip 32666862 2acd4614d9b95a3be4ca7b29bcee164d1b753f358a62441e50490c8fce533c30
fixed input mozc.data 36327368 9ef432c90b46b60aa7cd022176edc8e4b922c5855a1b6c8801a04e822683a4f8
fixed input pos_list.data 934 2e8e6a25423cea16a71eadd850e10936dfdd614d660680afe2b57713c9a9def4
fixed input dataset.json 9099 34c39deb04c94d5aa5efa1ed858fff050d487b87d240eb1112e6c2ab4863d6f6
fixed PC inventory regular files: 44 ELFs: 5 icon/notice: 36
fixed PC ELF paths: usr/bin/mozc_emacs_helper usr/lib/ibus-mozc/ibus-engine-mozc usr/lib/mozc/mozc_renderer usr/lib/mozc/mozc_server usr/lib/mozc/mozc_tool
fixed PC inventory outside verifier's equality check: tmp/icons.zip usr/share/emacs/site-lisp/emacs-mozc/mozc.el usr/share/ibus/component/mozc.xml
altered inventory accepted by compare_packages, ELF resolver mocked calls: 5 ELF headers/hash checked: 5
fixture outer regular members: ci/body_build.json ci/package-validation.json ci/package_build.json ci/tests.json dataset.json metadata.json mozc.zip
fixture metadata keys: dictionary kind package_format provenance recipe schema_version source_class system target tests
fixture candidate hashes: actual ZIP and metadata bytes match
fixture re-download byte mismatch rejected: Uploaded artifact bytes differ from the assembled outer ZIP

$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_SCRATCH/independent_checks.py" public
exit 0
Existing public-repo Actions specimen 44482523670 14510161453
candidate UI route anonymous GET 307 redirect_host=github.com
Actions REST download anonymous GET 401 redirect_host=None
candidate UI route anonymous final 404 mime=text/html zip_magic=False
```

The verification scripts above were run from owner-only scratch under
`$TMPDIR`. The one-off failure and record tests use fixture commands and
statuses. The listed PC data and Git objects are real.

## Command and artifact findings

- Workflow lines 25–45, 72–83 and `corrected_build.py:112–128` connect native
  runner labels, runtime host checks, and a numeric job ID. The API lookup
  uses the current run attempt, exact matrix job name and `in_progress`.
  The labels and endpoint are documented by GitHub. An actual job result is
  pending. [Native runner labels](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
  [Run-attempt job listing](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).
- The existing public command grammar executed successfully in the fixed PC
  check. It extracted the pinned PC file, verified the embedded data, prepared
  the EA2 overlay, validated original BUILD, overlay BUILD and encoder
  digests, and verified all seven notice snapshot records. Calls are
  `build_linux_dataset.py PC_ZIP DATASET_DIR`, `--verify-package ZIP MACHINE`,
  `--overlay EA2 DATASET_DIR OVERLAY` and
  `build_linux_metadata.py pc-snapshot EA2 SNAPSHOT DATASET_DIR`.
  The workflow then uses the overlay first in the package path for test,
  body and package. The producer invokes no dictionary generation CLI.
  Whether the full Bazel graph uses the overlay and retains the pinned
  bytes is pending native execution. [Bazel package-path semantics](https://bazel.build/versions/9.0.0/docs/user-manual#package-location).
- `corrected_build.py:86–109,153–177` checks tracked source, data and overlay
  before and after each command. It remeasures the recipe after the builds.
  `corrected_recipe.py:89–112` checks recipe HEAD, tree, tracked bytes and
  unchanged old helper digests. The corrected files are untracked here, so
  actual recipe Git provenance is pending a fixed recipe commit. The source
  and recipe checkouts and pre/post measurements are separate in code.
- The native test command uses `--nocache_test_results` and a build-event
  stream. `require_executed_test` requires per-attempt PASSED, rejects local
  and remote cache hits, and requires one PASSED summary. Unit fixtures cover
  absent results and both cache hits. Body uses the workspace binary labels
  from `deps(//unix:package)`, then package builds `//:package`. The test and
  build members carry actual command return codes in execution, source/tree,
  PC data digest and source fingerprints. In fixtures the members bind to
  metadata by path and SHA-256, and failed, changed and tampered evidence
  is rejected. No native Bazel commands were run in this verifier.
  [Bazel 9 CLI reference](https://bazel.build/versions/9.0.0/reference/command-line-reference).
  [Bazel 9.0.2 build-event schema](https://github.com/bazelbuild/bazel/blob/9.0.2/src/main/java/com/google/devtools/build/lib/buildeventstream/proto/build_event_stream.proto).
- Target `src/.bazeliskrc:1` pins Bazel 9.0.2. In target
  `src/.bazelrc:6–10,32–34,75–77`, Linux defaults select the OSS Linux
  environment. Explicit `release_build` sets `--compilation_mode=opt` and
  `--copt=-DABSL_MIN_LOG_LEVEL=100`. Target `.github/workflows/linux.yaml:73–76`
  uses that profile for package and line 115 tests with `-c dbg`. The corrected
  producer uses the default mode for all stages with `--strip=never`. Build
  config options are inherited by `test` if selected. Release mode affects
  optimization and compile-time Abseil logging. The definition contains no
  test-skipping option. Native output equivalence between these modes is
  unmeasured. [Bazel rc inheritance](https://bazel.build/versions/9.0.0/run/bazelrc).
  [Abseil logging conditions](https://github.com/abseil/abseil-cpp/blob/master/absl/log/internal/conditions.h).
- Producer outer layout is root `mozc.zip`, `dataset.json`, `metadata.json`,
  `ci/tests.json`, `ci/body_build.json`, `ci/package_build.json`,
  `ci/package-validation.json` and `notices/<snapshot member>`. The
  package-validation record holds ABI, overlay, embedded and notices
  measurements. ABI inspection code checks machine 62 or 183, ELF64 little
  endian, five paths, and declares interpreter, NEEDED and closure for the
  Emacs helper. Actual corrected bytes and native dependency resolution are
  pending. The separate records upload carries `candidate.json` and
  `upload.json`. The producer requests v7 raw single-file upload
  (`archive: false`) and compares locally assembled, remote-fetched and
  action-reported digests, then hashes metadata bytes in the fetched ZIP.
  The local fixture used two real ZIP byte streams and rejected a one-file
  byte difference. Metadata 2 contains no outer hash, upload ID, final manifest
  hash or downstream gates in the fixture or builder return object.
  [GitHub raw single-file upload](https://github.com/actions/upload-artifact/tree/v7#upload-an-individual-file-unzipped).

## Unverified

Actual GitHub dispatch, runner and job IDs, fixed recipe Git provenance, the
uncached Linux test and body/package builds on both systems, corrected ABI and
embedded data/POS, remotely uploaded corrected ZIP bytes/hash and candidate
values, and downstream private runtime, screen, consumer and promotion gates.
The source guard is a reproduced pre-build failure. Corrected native CI,
guest operations, and Git write actions were outside this verifier run.
