# Manual native Linux packages for minamorl/mozkey

P1 prepares CI; it does not demonstrate a successful Linux/ARM build. No CI
dispatch, Git delivery, repository settings mutation, dotfiles edit, tablet/guest
operation, or input-method restart was performed in this lane. Actual native
builds and inspection of their real archives belong to P2.

The inherited Linux workflow already separates dictionary preparation from
packaging. Keeping that boundary lets a control and a UI fix consume identical
dictionary bytes instead of regenerating them on different machines.

## Frozen behavior / decisions

- Only `.github/workflows/minamorl-linux-build.yml` is added; inherited workflows
  and settings remain intact. Its only trigger is `workflow_dispatch`. Both jobs
  require the public repository `minamorl/mozkey`; no upstream workflow is called.
- Required `source_sha` and optional `control_sha` accept full lowercase 40-digit
  commit SHAs only. Both are checked out from the literal fork repository.
  The build scripts come from the dispatched workflow revision, independently of
  the source revision under test. No local source patches are applied by CI.
- One `ubuntu-24.04` job generates the existing SHA256-locked daily dictionaries
  using `tools/dictionary/prepare_daily_dictionary.ps1`. Missing/inaccessible
  locked sources fail the job; there is no substitute source, sample fallback,
  or lock update. PowerShell must already exist on this x64 hosted image.
- The four generated dictionaries and their manifest are uploaded once and
  reused by every build in this run. **Before that first upload**, snapshot
  creation validates the target source SHA's required notice Git blobs and
  includes them under `documents/`. The schema-2 `dictionary.json` records their
  source paths and SHA256s; recursive `SHA256SUMS` includes the documents, input
  lock, dictionaries and manifest. Creation verifies the complete snapshot
  before returning success. Missing, empty or non-regular source notices fail
  before the snapshot output directory is created, regardless of whether a
  binary is ever built. The downstream job checks the manifest SHA256 received
  through a job output, every dictionary/notice digest, the exact document file
  set, the notice source identity and the checksum manifest before restore/build.
  Snapshot schemas without required notices are rejected. Tracked
  dictionary/build configuration inputs must match between source/control,
  including every tracked Bazel-named file, `*.bzl`, `BUILD`, and `WORKSPACE`,
  not only root-level configuration. This includes `.bazelignore` and any tracked
  `MODULE.bazel.lock`, as well as rc/version and per-target definitions.
  All notice source paths are included in this identity too, so control/fix
  comparisons share the dictionary's accompanying attribution bytes.
  Dictionary/data-manager generator code and tracked scripts under `src/` are
  included too, so identical input text cannot hide changed dictionary filters.
  CI tooling under `tools/minamorl` is hashed from the separate recipe checkout,
  not from either source tree; adding CI tooling to the fork must not invalidate
  comparison with the pre-CI control revision.
  Even test-only BUILD changes are intentionally rejected; comparison commits
  must share their configuration.
  Restore refuses existing generated files, unexpected files, or changed locks.
- Native builds use only standard public `ubuntu-24.04` (x86_64) and
  `ubuntu-24.04-arm` (aarch64). No cross compiler, guest Bazel, self-hosted runner,
  larger runner, new cache account, or new infrastructure is used.
- At most two builds run concurrently. A single-source run has two builds; an
  optional distinct control adds two more. Dictionary job timeout: 60 minutes;
  each build: 100 minutes, with a 90-minute build step and 80-minute Bazel process
  timeout. Downloads and preparation are also bounded. No automatic CI retry.
- Bazelisk 1.27.0 and Bazel 9.0.2 native downloads are checksum pinned. The source
  `.bazeliskrc` must still specify 9.0.2. The recipe is the inherited
  `build package --config release_build`, with `--jobs=2`, batch mode, isolated
  output root, and system/home rc files disabled. Android NDK config is unset.
- Actions use immutable commit pins, `contents: read`, and checkout with
  `persist-credentials: false`. Artifacts contain only explicit package/dictionary
  files and whitelisted metadata, never `.git`, runner homes, environment dumps,
  caches, or credentials. Artifacts are retained for seven days. Public artifact
  storage quotas still apply; do not add storage or paid runners as a fallback.
- Both the dictionary snapshot and each binary artifact carry required attribution documents under `documents/`,
  read from Git blobs at the **build source SHA**, independently of the workflow
  recipe revision and uncommitted files. Missing, empty or non-regular notice
  sources fail snapshot creation/packaging before its output directory is created. Document paths,
  source paths and SHA256s are recorded in metadata and recursive `SHA256SUMS`.

## Package contract for later Nix consumption

The source at `fa5a1bed5cbfc2f4de2d216fa3721fd6dcbf5091` uses **mozkey** paths,
not the older mozc path assumptions. `src/config.bzl`, `src/unix/BUILD.bazel`, and
`src/unix/build_package.py` currently describe this inner `mozc.zip` layout:

```text
usr/lib/mozkey/{mozc_server,mozc_tool,mozc_renderer}
usr/lib/ibus-mozkey/ibus-engine-mozkey
usr/share/ibus/component/mozkey.xml
usr/share/ibus-mozkey/...
usr/share/icons/mozkey/...
```

The helper checks these required files, nonempty icon trees, ZIP CRCs, duplicate
entries (including normalized extraction destinations), file/directory and
ancestor-file conflicts, traversal, non-regular payloads, symlinks, ELF64 little-endian executable
headers (current version, 64-byte header, ET_EXEC or ET_DYN) and native
ELF machine IDs (62 / 183). An unexpected layout fails; it is not silently
renamed or repacked. This is a source-derived contract plus synthetic validator
coverage, **not an inspection of a real built archive**. P2 must open
`archive-files.txt`, inspect XML paths and ELF dependencies, and verify the ZIP
itself before Nix packaging. Ubuntu/glibc linkage is not proof of NixOS runtime
compatibility. Nothing here changes dotfiles references or installs the package.

Each `mozkey-linux-<arch>-<source|control>-<full SHA>` artifact contains:

```text
mozc.zip
metadata.json
dictionary.json
daily_sources.json
archive-files.txt
package-versions.txt
documents/DICTIONARY_NOTICES.md
documents/SudachiDict-LEGAL.txt
documents/credits_en.html
documents/Mozc-dictionary-README.txt
documents/THIRD_PARTY_NOTICES.md
documents/LICENSE
documents/LICENSE-2.0.txt
SHA256SUMS
```

`metadata.json` records source SHA, inner archive SHA256, architecture, dictionary
manifest SHA256, individual dictionary digests, lock hash, build input identity,
recipe file hashes/command, Bazel/Bazelisk versions, workflow revision, runner
image, run ID/attempt, and UTC creation time. `SHA256SUMS` covers all payload
files recursively, including the documents, except itself. The document metadata
identifies each original path and target source SHA. `LICENSE-2.0.txt` is the
Apache-2.0 text already tracked under the Windows installer's license directory;
no platform-specific runtime binaries or runtime claims are copied with it.
Keep `documents/` with the binaries in downstream packaging/installations;
redistributing the inner `mozc.zip` alone drops these notices. This helper leaves
the inner ZIP unchanged. GitHub's outer artifact digest is distinct from the inner
`mozc.zip` digest. These hashes provide traceability, not signed attestation or
bit-for-bit reproducibility: apt versions and hosted images can change. Compare
control/fix from the same run and arch, verify their dictionary/recipe hashes,
and also compare `package-versions.txt` and runner-image metadata.

The earlier `minamorl-linux-dictionary` artifact is a separate distribution and
contains the four dictionary text files, `daily_sources.json`, schema-2
`dictionary.json`, the same `documents/` file set and recursive `SHA256SUMS`.
Its notices are validated and included before upload; a failed later binary
build cannot leave a publicly downloadable dictionary artifact without notices.
Restore rejects missing, modified, symlinked or extra notice files and modified
checksum manifests. Keep the notices with a redistributed dictionary snapshot.

## Root-only publication / registration procedure (not executed)

Before publishing, review the staged diff and the local gate log. Root owns
commit, push, integration and dispatch. Do not use an upstream remote. Verify
that the fork is still public and that current inherited workflow triggers have
not gained events that `[skip ci]` does not cover.

**Before the first manual dispatch**, root checks the distribution instructions
at the selected source SHA (`src/data/installer/DICTIONARY_NOTICES.md`, including
its redistribution section, and `THIRD_PARTY_NOTICES.md`). Public-fork workflow
artifacts are downloadable by signed-in users with repository read access, so a
test/manual artifact upload is already distribution; do not defer this check to
a later release. Review the final dictionary sources/data reuse terms, especially
Wikipedia use and scraped encyclopedia data, and applicable Qt LGPLv3 binary
distribution obligations identified by the repository notice. Record the
publication determination before dispatch; do not treat the presence of notices
as proof of legal clearance. This lane bundles existing documents and does not
perform or assert that determination. No CI dispatch is performed here.

At the inspected base, inherited workflows use `push`, `pull_request`,
`workflow_dispatch`, and `workflow_call`; no `pull_request_target`, `workflow_run`,
`schedule` or `release` trigger exists. Several Linux/Windows/macOS/Android/lint
workflows use unrestricted `on: push`. An empty workflow API list does not mean
these files cannot run after publication.

The documented GitHub skip instruction applies only to `push`/`pull_request`,
not manual dispatch. Put `[skip ci]` on **every pushed tip and integration/merge
commit** in this registration operation. Do not remove workflows or disable
repository settings. Do not open a PR, issue, or comment against the upstream.
If new unsuppressible triggers or required skipped checks are found, stop and
resolve with root rather than treating this procedure as permission to bypass
protection.

In this lane, root may stage and commit the owned files after review:

```bash
git add -- .github/workflows/minamorl-linux-build.yml tools/minamorl/build*
git diff --cached --check
git diff --cached --stat
git commit -m 'ci: prepare manual fork Linux packages [skip ci]'
```

The workflow must be on the fork's **default branch** for first manual dispatch.
After fetching current fork main and integrating this lane in root's integration
worktree, use a merge commit whose message also contains `[skip ci]` (if a merge
is needed):

```bash
# Run in root's clean, verified integration checkout of fork main.
git fetch https://github.com/minamorl/mozkey.git main
git merge --ff-only FETCH_HEAD
git merge --no-ff lane/mozkey-linux-ci-20261119 \
  -m 'ci: register manual fork Linux packages [skip ci]'
git log -1 --format=%B
git push https://github.com/minamorl/mozkey.git HEAD:refs/heads/main
gh workflow list --repo minamorl/mozkey --all
gh workflow view minamorl-linux-build.yml --repo minamorl/mozkey
gh run list --repo minamorl/mozkey --limit 20
```

If the fast-forward fails, root resolves the divergence normally and reruns
gates before publishing. Never force push. If main advanced, integrate and
rerun gates. Root selects the ordinary protected path if required, preserving
the skip instruction on the final pushed tip. Observe the run list after
registration: no inherited build should have started. A workflow reported as
disabled must be handled explicitly by root before dispatch; this lane changes
no enabled/disabled state.

Then P2 can dispatch this workflow only. Substitute root-verified full source
SHAs; for an initial single-source build omit the `control_sha` flag. `main`
selects the reviewed workflow revision, while the input SHAs select build code:

```bash
gh workflow run minamorl-linux-build.yml --repo minamorl/mozkey --ref main \
  -f source_sha=FULL_FIX_OR_FEASIBILITY_SHA \
  -f control_sha=FULL_DISTINCT_CONTROL_SHA
gh run list --repo minamorl/mozkey \
  --workflow minamorl-linux-build.yml --event workflow_dispatch --limit 5
gh run watch RUN_ID --repo minamorl/mozkey --exit-status
gh run view RUN_ID --repo minamorl/mozkey --log-failed
gh run download RUN_ID --repo minamorl/mozkey \
  --name mozkey-linux-aarch64-source-FULL_SOURCE_SHA --dir ARTIFACT_DIRECTORY
```

Select the exact run ID from the returned workflow/event/SHA, not just the newest
unrelated run. Download both arches (and control if requested), run
`sha256sum --check SHA256SUMS` in each payload directory, compare metadata, and
inspect the real ZIP. A green CI build alone does not establish installation or
the candidate-window fix. No tablet operations are part of this procedure.

## Local gates / evidence

Run with installed ShellCheck and actionlint, using a writable task-local
`TMPDIR` if the host's default temporary directory is sandbox-inaccessible:

```bash
bash tools/minamorl/build_linux_check.sh /absolute/path/to/actionlint
```

The check script runs actionlint only on the new workflow, ShellCheck, Bash syntax,
stdlib tests of the existing icon/package builders' real directory structure
(with synthetic executable bytes), successful handoff/native manifests, corruption, changed
config, wrong-arch, traversal, normalized ZIP destination/ancestor conflicts,
target-revision attribution documents/checksums, and the non-Actions
execution guard. It asserts the guard's exact exit code/message separately for
wrong repository and non-Actions inputs, so unrelated platform/argument failures
cannot count as a passing guard test. It does not run Bazel or provision a machine. Raw local output
is in `build-linux-local-gates.log`; command exit status is recorded in the lane
report. Synthetic ELF/ZIP fixtures are not actual build artifacts.

Local actionlint 1.7.12 was downloaded from its official release as
`actionlint_1.7.12_darwin_arm64.tar.gz`, SHA256
`aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f`.
The download used the ignored worktree `dist/minamorl-ci-tmp` as task-local
`TMPDIR`; no other checkout or guest was used.

The first checkpoint review caught rejection of the existing icon builder's
`white/` and `outlined/` directory entries. The validator now accepts descendants
of the two allowed icon roots, and a regression invokes the existing builders
to exercise their actual ZIP structure while retaining path/symlink rejection.
The next checkpoints expanded configuration identity to all tracked Bazel files
and made guard tests check their specific failure. A renderer-target regression
now rejects removal of `ENABLE_QT_RENDERER` between the comparison commits.
Regressions also reject one-sided additions/changes of `.bazelignore` and a
tracked `MODULE.bazel.lock`. Bash syntax checks run once per shell script.
Dictionary generator code is also compared; ELF fixtures now carry ET_EXEC or
ET_DYN headers, and ET_NONE/ET_REL/ET_CORE are rejected. These checks identify
the declared binary format; they do not load/execute it or prove dynamic linkage.
Independent handoff review additionally required target-source attribution
documents and normalized destination checks. Regressions cover file-versus-dir
names (`white` / `white/`), implied directory conflicts, dot/separator aliases in
both entry orders, missing/empty notices, and target-revision document bytes.
The next handoff review also covered the earlier dictionary upload: schema 2
requires notices in the snapshot itself. Regressions check notice inclusion and
hashes, empty/missing/symlink source rejection before output creation, and
downloaded notice/checksum tampering before restore. No live upload was run.

## Primary sources read for P1

Retrieved 2026-11-19 UTC. These are retrieval dates, not asserted release dates.

- [Fork Linux build guide at the source SHA](https://github.com/minamorl/mozkey/blob/fa5a1bed5cbfc2f4de2d216fa3721fd6dcbf5091/docs/build_mozc_for_linux.md)
  and [inherited workflow](https://github.com/minamorl/mozkey/blob/fa5a1bed5cbfc2f4de2d216fa3721fd6dcbf5091/.github/workflows/linux.yaml).
- [Upstream Linux build guide](https://github.com/google/mozc/blob/master/docs/build_mozc_for_linux.md).
- [GitHub standard runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners):
  public x64 and ARM64 standard Linux labels, each 4 CPU / 16 GB RAM / 14 GB SSD;
  standard compute is free on public repositories. Availability is feasibility,
  not evidence of a completed build or guaranteed sufficient disk.
- [Manual dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
  and [skip semantics](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs).
- [Artifact download access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts):
  signed-in users with repository read access can download artifacts. Read
  during the attribution/handoff review follow-up on 2026-11-19 UTC.
- [Checkout pinned README](https://github.com/actions/checkout/blob/d23441a48e516b6c34aea4fa41551a30e30af803/README.md),
  [upload pinned README](https://github.com/actions/upload-artifact/blob/b7c566a772e6b6bfb58ed0dc250532a479d7789f/README.md),
  [download pinned README](https://github.com/actions/download-artifact/blob/018cc2cf5baa6db3ef3c5f8a56943fffe632ef53/README.md).
  The corresponding v6 refs were resolved using the public GitHub git-ref API.
- [Bazelisk 1.27.0 README](https://github.com/bazelbuild/bazelisk/blob/v1.27.0/README.md)
  and [release asset digests](https://api.github.com/repos/bazelbuild/bazelisk/releases/tags/v1.27.0):
  native `bazelisk-linux-amd64` / `bazelisk-linux-arm64` exist; their SHA256s
  are embedded in `build_linux.sh`. Bazelisk supports `BAZELISK_VERIFY_SHA256`.
- [Bazel 9.0.2 ARM64 checksum](https://releases.bazel.build/9.0.2/release/bazel-9.0.2-linux-arm64.sha256)
  and [x86_64 checksum](https://releases.bazel.build/9.0.2/release/bazel-9.0.2-linux-x86_64.sha256),
  matching the official GitHub release asset digests.
- [actionlint usage](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/usage.md)
  and [release digests](https://api.github.com/repos/rhysd/actionlint/releases/tags/v1.7.12).
- [Bazel ignore configuration](https://bazel.build/versions/9.0.0/run/bazelrc#bazelignore)
  and [module lockfile](https://bazel.build/external/lockfile), read while closing
  configuration-identity review findings.
- [ELF header specification](https://gabi.xinuos.com/elf/02-eheader.html), including
  ET_EXEC / ET_DYN and ELF64 header fields.

Remaining P2 risks: dictionary source availability, standard-runner disk/RAM/time
capacity, actual ARM Bazel/toolchain support, real package layout, and NixOS
runtime dependencies. No paid upgrade or unrelated workflow execution is an
automatic response to those failures.
