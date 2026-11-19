# Unpatched PC revision: CI compatibility handoff

Target code is exactly `koyasi777/mozkey` commit
`ea2e9f5cfb80fccf50caca1af097b2f0830be52a`, built through the owner's fork.
This is the PC revision requested for the tablet. No keymap requirement is
weakened and no startup migration, candidate-window fix, UI change or source
patch is applied. This lane performs code plus local fixture checks only.
No Git command, CI dispatch, guest/tablet operation, dotfiles change, secret
access or repository-settings mutation is part of this run.

## Frozen inputs and gate

- `mozkey-fork-distribution.spec` 0.2 SHA256
  `a0069fed0c69cc3ac3829c98386db18e1d774c58e1ecf84403fce8e76ffc42a4`.
- `zeno-android-refresh.spec` 0.5 SHA256
  `795709047c659c48ea5ca6bd67928e59fc2934bea176fcc407cdbfe7e6373317`.
- Imported `house_style` 4.0 / `prohibitions` 1.0 were read. The two supplied
  specs passed sat-check before implementation (112/108 and 112/107 pins/keys).
- The newest instruction fixes the unpatched initial source to the full SHA
  above; it does not identify it as a later repaired revision. PC keymap
  equivalence and tablet usability still require the receiving lane's checks.

## Primary evidence

Retrieved from immutable official raw URLs under
`https://raw.githubusercontent.com/koyasi777/mozkey/ea2e9f5cfb80fccf50caca1af097b2f0830be52a/`
on 2026-11-19 UTC. `build_linux_ea2e9f5c_sources.json` records the full source SHA,
repository and SHA256 of every saved primary file. The sibling
`build_linux_ea2e9f5c_sources/` directory contains those exact bytes, including
copyright headers. `build_linux_primary.py` reproduces this small source fetch;
it neither clones nor builds a repository.

The target Linux workflow uses Ubuntu 24.04, `libibus-1.0-dev`, `qt6-base-dev`
and `bazelisk build package --config release_build`. Its `.bazeliskrc` selects
Bazel 9.0.2, so the existing checksummed Bazelisk 1.27.0 / native Bazel 9.0.2
downloads are retained unchanged. Their existence and compatibility are separate
from the pending native build of this revision.

`src/config.bzl`, `src/unix/BUILD.bazel`, the package/icon generators and IBus XML
generator/BUILD define the old installation layout:

```text
usr/lib/mozc/{mozc_server,mozc_tool,mozc_renderer}
usr/lib/ibus-mozc/ibus-engine-mozc
usr/share/ibus/component/mozc.xml
usr/share/ibus-mozc/...
usr/share/icons/mozc/...
usr/bin/mozc_emacs_helper
usr/share/emacs/site-lisp/emacs-mozc/mozc.el
tmp/icons.zip
```

`mozc` is this Mozkey revision's own package naming, not a substitution with
nixpkgs Mozc. The upstream packager puts `icons.zip` at the relative ZIP member
`tmp/icons.zip`; fixtures use a private task TMPDIR and never write OS `/tmp`.
The inner package ZIP is preserved byte-for-byte, including these known extras.
The receiver must account for this old layout rather than rename the source.

`build_linux_compat.py` enables this format only for the exact requested SHA,
and verifies the SHA256 of nine defining source files before using it in CI.
The ZIP validator requires an exact legacy file set: the four main ELF64s,
the known Emacs helper/client, component, exact icons and inner icon ZIP. It
checks the Emacs helper's native machine ID as well as the four required main
ELFs. The component's identity, `--ibus` and dynamic `--xml` executable paths
must match the old source. The inner icons must be safe regular files/directories
and match installed icon bytes; the IBus PNG copies must match too. Mixed old/new
layouts, unknown payload, traversal, normalized collisions, symlinks, nonregular
files, wrong arch and invalid ELF headers remain rejected. The current modern
layout remains a separate format.

## Dictionary and notice compatibility decisions

The old revision already selects `mozcdic_ut_daily_local`, but its group uses
`glob(..., allow_empty=True)`. CI still requires and hashes all four generated
dictionary inputs before build; it does not accept a dictionary silently missing
those files. This source lacks the modern `daily_sources.json` lock and the two
additional dictionary notice documents. Its old importer uses moving external
inputs, so the adapter does not execute that unpinned importer.

For this exact source only, the existing **locked preparation** runs in the
separate reviewed workflow `recipe` checkout. That checkout must be clean,
its HEAD must equal `GITHUB_SHA`, and preparation must leave tracked files
unchanged. The old build checkout is independently required clean and remains
unpatched. Only the four generated dictionary text files are restored into its
untracked generated-data directory. No lock, BUILD, source, keymap, notice or
version file is written into the old checkout.

Legacy dictionary manifest schema 3 records the old build source/format, its
tracked-input identity, the preparation recipe revision/input identity and the
actual external lock/data hashes. Snapshot validation binds preparation back
to the trusted workflow checkout. Five existing notices come from the target
source; `DICTIONARY_NOTICES.md` and `SudachiDict-LEGAL.txt` are supplemental
distribution notices from the locked recipe. Every notice records its own source
repository/revision/path/hash. Supplemental documents are not claimed to exist
in the old source. Both first-upload dictionary artifact and binary artifact
include all notices and recursive checksums. Modern schema 2 is preserved.

This fixes the compiled source version while retaining explicitly frozen external
dictionary data. It does not assert that PC dictionary data or learned history
are byte-identical to those used by a new CI run. The PC keymap requirement is
unchanged. The existing publication/distribution review for dictionary data and
Qt still applies before any public artifact upload.

## Root-owned delivery and dispatch

Root must independently accept these files, run its Git-enabled full gates and
publish the new recipe to fork main before dispatch. The reported existing main
`742a0d8` has the previous modern-only adapter; running that recipe unchanged
against the old source is not supported. Preserve inherited workflows and
the existing `[skip ci]` publication procedure. This worker does not perform
Git operations, open PRs, dispatch workflows or change Actions settings.

After the new recipe is merged into fork main, the exact initial-build inputs are:

```bash
gh workflow run minamorl-linux-build.yml --repo minamorl/mozkey --ref main \
  -f source_sha=ea2e9f5cfb80fccf50caca1af097b2f0830be52a
```

Omit `control_sha` for this initial unpatched build. The existing workflow builds
both native standard hosted labels (`ubuntu-24.04`, `ubuntu-24.04-arm`), at most
two builds concurrently. It does not use a paid larger runner or a guest build.
The ARM artifact name is:

```text
mozkey-linux-aarch64-source-ea2e9f5cfb80fccf50caca1af097b2f0830be52a
```

Before installation the receiver must verify the real run result, metadata
source/format, `SHA256SUMS`, inner archive hash, actual native ELF dependencies,
legacy XML/icons and the unchanged PC keymap. No real run ID/archive hash is
invented by this lane. Once CI starts, root sends Main TL a one-line refreshed
ETA; the previous 19-minute observation is not a duration guarantee.

For the later investigation, retain the requested comparison of ea2e9f5c through
fa5a1bed: startup keymap migration and candidate-window changes may matter. This
lane neither imports those changes nor identifies a flicker cause.

## Local checks and remaining acceptance

`build_linux_compat_test.py` is Git-free: committed-tree operations are simulated
in memory; actual saved upstream icon/package/XML generators run with synthetic
ELF/data fixtures in task-local TMPDIR. No heavy build is run. It checks the
format fingerprint, both native arch headers, old/current/mixed formats, source
immutability, separate preparation/notice provenance and original archive hash.

The original worker run used the Git-free subset. The clarified boundary permits
Git in synthetic TMP fixture repositories and read-only target diffs, while
repository delivery/mutation stays with root. The full `build_linux_check.sh`
includes both original provenance tests and additional compatibility fixtures.
No separate worker or background process is started. Original local output is saved under
`build_linux_ea2e9f5c_checks.log`.

The Git-free worker gate used here is:

```bash
mkdir -p tools/minamorl/build_linux_tmp
export TMPDIR="$PWD/tools/minamorl/build_linux_tmp"
bash tools/minamorl/build_linux_compat_check.sh /absolute/path/to/actionlint
rmdir tools/minamorl/build_linux_tmp
```

The public gate enumerates owned regular files recursively, including untracked
files in `build*` directories. Symlinks and special files are rejected. It does
not send a directory directly to `git diff --no-index` or exempt unrelated
fixture directories from whitespace checks.

The only pristine-source whitespace exception is the exact
`build_linux_ea2e9f5c_sources/` tree. Before applying it, the gate verifies the
manifest SHA256
`111b5338b0b0c6bb9004cd030e7d522141b97082054029eab88bb92a7824304f`,
the fixed `koyasi777/mozkey` source SHA, the exact expected file set, regular file
types and every content SHA256. The gate prints the origin and per-file hashes.
The official README's trailing blank line is retained byte-for-byte; the copy
is never trimmed or rewritten to satisfy style. A changed manifest, missing or
extra primary file, or any changed byte fails the gate before style exemption.

`build_linux_gate_test.py /absolute/path/to/actionlint` runs the entire public
gate in a disposable TMP synthetic repo, with both staged pristine sources and
an untracked nested owned file. It also checks rejection of a trimmed dictionary
README, changed manifest, extra primary file and nested owned trailing whitespace.
Negative-probe stdout is preserved losslessly as a JSON string so its intentional
bad whitespace does not corrupt the transcript's own style. Commands, exit codes
and the full public-gate output are in `build_linux_ea2e9f5c_public_gate.log`.

Independent acceptance should inspect: the pinned source-to-format binding;
the known old extras without weakening unsafe/unknown/mixed rejection; the
separate locked dictionary producer and supplemental-notice origins; and the
actual source/ZIP hashes on the eventual native CI run. Actual CI build success,
ARM artifact availability, installation, runtime dependencies and keymap
acceptance remain unverified here.
