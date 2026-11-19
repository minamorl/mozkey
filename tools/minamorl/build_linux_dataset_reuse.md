# EA2: reuse the existing PC dictionary, never regenerate it

This recipe supersedes the daily-dictionary generation instructions for the
initial unmodified EA2 tablet build. The workflow file is unchanged. Its job
named `dictionary` now downloads/extracts existing PC bytes and supplies a
schema-4 snapshot; it does not invoke PowerShell, the importer, or a dictionary
builder. A missing release asset or mismatched hash fails without fallback.

## Actual input and evidence

- Source: `ea2e9f5cfb80fccf50caca1af097b2f0830be52a`, unchanged.
- PC: `ssh nixos`, original ZIP
  `/nix/store/ma0ab4ipfnz1hqhm3xq3qhkbnfdl0y2l-mozkey-ea2e9f5c-linux.zip`.
- ZIP SHA256:
  `2acd4614d9b95a3be4ca7b29bcee164d1b753f358a62441e50490c8fce533c30`.
- Server's `kOssMozcDataSet_data`: 36,327,368 bytes, version `24.11.oss`, SHA256
  `9ef432c90b46b60aa7cd022176edc8e4b922c5855a1b6c8801a04e822683a4f8`.
- Tool's `kPosArray_data`: 934 payload bytes plus 2 padding bytes, SHA256
  `2e8e6a25423cea16a71eadd850e10936dfdd614d660680afe2b57713c9a9def4`.
- Full receipt: `build_linux_dataset_receipt.json`, SHA256
  `34c39deb04c94d5aa5efa1ed858fff050d487b87d240eb1112e6c2ab4863d6f6`.
- Raw remote `sha256sum`, `nm -S -C`, `readelf -SW`:
  `build_linux_dataset_elf_probe.log`.
- Actual extraction: `build_linux_dataset_probe.log`. Actual EA2 byte encoder
  output hashes: `build_linux_dataset_overlay_probe.log`.

The ZIP was copied back to this worktree's ignored temporary area:
`dist/minamorl-ci-tmp/dataset-reuse.5WCibl/inputs/pc-linux.zip`.
The extracted bytes, fetched primary files, and encoded includes are alongside
it under `pc-data/`, `ea2-source/`, and `overlay/`. These are temporary inputs,
not source changes. The receipt and raw logs above are the retained evidence.

The successful fork run `44460070226` snapshot describes fa5 rather than EA2;
it is not used as an equivalent PC dictionary.

## Primary source and native data boundary

Read immutable upstream files at
`https://raw.githubusercontent.com/koyasi777/mozkey/ea2e9f5cfb80fccf50caca1af097b2f0830be52a/`:

- `src/data_manager/oss/oss_data_manager.cc` loads the embedded serialized
  `kOssMozcDataSet`; `src/data_manager/oss/BUILD.bazel` declares its data/header.
- `src/data_manager/mozc_data.bzl` distinguishes dictionary generation from
  byte-to-C++ embedding. `src/build_tools/embed_file.py` embeds existing bytes
  as little-endian uint64 constants with the original byte length.
- `src/data_manager/dataset.proto`, `dataset_writer.cc`, and `src/base/util.cc`
  specify the footer: metadata length, SHA1, big-endian whole-data length.
- `src/base/container/serialized_string_array.cc` specifies the little-endian
  count/offset/length layout for the separately embedded POS names.

The extractor validates ELF64 LE, symbol identity/count/type, section bounds,
dataset magic, footer length/SHA1, entry names/ranges/order, version, POS string
offsets/terminators, and fixed ZIP/data/POS/receipt SHA256. Both target
architectures are little-endian; the serialized data is reused byte-for-byte.

The actual EA2 encoder was run on the actual extracted data locally. This was
only byte encoding; no lexical generation, C++ compilation, or Bazel build ran.

The body build uses Bazel 9.0.2's ordered partial source roots:
`--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%`. Official option
definition read:
`https://raw.githubusercontent.com/bazelbuild/bazel/9.0.2/src/main/java/com/google/devtools/build/lib/pkgcache/PackageOptions.java`.
Only the temporary `data_manager/oss` package supplies the existing `.data` and
encoded includes. It replaces the `mozc_dataset(...)` invocation with source
filegroups having the existing target names. All original C++ sources/headers
and library definitions are preserved. The checkout and its keymap/config
remain unchanged; source `git diff HEAD` checks still run. Overlay BUILD,
encoder, and every overlay input hash are recorded separately, without changing
the nine-file EA2 package-format binding.

`--strip=never` retains object symbols for the post-build proof. The strict
package validator still checks the original format, all five ELF objects
(four main binaries and Emacs helper), correct native machine, component,
icons, nested icon ZIP, unknown/mixed payload, unsafe paths and symlinks.
After those checks, it extracts the native server dataset and tool POS bytes
and requires the PC hashes above. Source-format and tool-download locks remain.

The schema-4 snapshot contains `mozc.data`, `pos_list.data`, `dataset.json`,
`dictionary.json`, all seven existing notice documents and `SHA256SUMS`.
Notices and their origin/hash are verified before the first artifact upload,
and again before packaging. Final metadata records the source SHA, archive SHA,
architecture, recipe, exact snapshot, overlay hashes and embedded payload
hashes. The final artifact also includes notices and checksums.

## Root: publish the existing input, then dispatch the accepted recipe

These are instructions only. This worker did not publish, commit, push,
cancel, dispatch, change settings, or operate the tablet.

After independent acceptance, use the existing root Git delivery gate. Publish
only the accepted recipe to the own fork, with `[skip ci]` in the delivery
commit as already documented to avoid inherited push jobs. Source input stays
EA2; do not dispatch the old recipe from `742a0d8`.

Before publishing the input, verify its fixed hash and pristine notice copies:

```sh
cd /Users/minamorl/repos/.worktrees/mozkey/mozkey-linux-ci-20261119
shasum -a 256 dist/minamorl-ci-tmp/dataset-reuse.5WCibl/inputs/pc-linux.zip
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_primary.py --check-whitespace
cp dist/minamorl-ci-tmp/dataset-reuse.5WCibl/inputs/pc-linux.zip dist/minamorl-ci-tmp/dataset-reuse.5WCibl/inputs/mozkey-ea2e9f5c-linux.zip
```

Publish the ZIP together with the existing target-source notices and the two
supplemental notices; these are public redistribution materials, not secrets.
The five pristine files are fixed to EA2 by the reviewed primary manifest.
The two supplemental documents retain their recipe-revision origin in CI.
Review their existing distribution conditions before this first publication.
Do not trim/rewrite the pristine files. Do not use `--clobber`; a pre-existing
tag/asset requires checking the existing bytes rather than overwriting it.

```sh
gh release create ci-pc-ea2e9f5c-data \
  --repo minamorl/mozkey --target main \
  --title 'Existing EA2 PC dictionary input' \
  --notes 'Unmodified EA2 PC input; ZIP SHA256 2acd4614d9b95a3be4ca7b29bcee164d1b753f358a62441e50490c8fce533c30. Dictionary data is reused, never regenerated. Attached license and attribution documents accompany this input.' \
  dist/minamorl-ci-tmp/dataset-reuse.5WCibl/inputs/mozkey-ea2e9f5c-linux.zip \
  tools/minamorl/build_linux_dataset_receipt.json \
  tools/minamorl/build_linux_ea2e9f5c_sources/LICENSE \
  tools/minamorl/build_linux_ea2e9f5c_sources/THIRD_PARTY_NOTICES.md \
  tools/minamorl/build_linux_ea2e9f5c_sources/src/data/installer/credits_en.html \
  tools/minamorl/build_linux_ea2e9f5c_sources/src/data/dictionary_oss/README.txt \
  tools/minamorl/build_linux_ea2e9f5c_sources/src/win32/installer/zenz_runtime/licenses/Apache-2.0.txt \
  src/data/installer/DICTIONARY_NOTICES.md \
  src/data/installer/SudachiDict-LEGAL.txt
```

The fixed download URL is
`https://github.com/minamorl/mozkey/releases/download/ci-pc-ea2e9f5c-data/mozkey-ea2e9f5c-linux.zip`.
No release API credentials are required by the runner: the input is public and
the runner's fixed SHA256 check remains mandatory.

If the old run is still generating, root can cancel it before the replacement;
the workflow concurrency setting otherwise queues the replacement behind it.

```sh
gh run cancel 44474078274 --repo minamorl/mozkey
gh workflow run minamorl-linux-build.yml --repo minamorl/mozkey --ref main \
  -f source_sha=ea2e9f5cfb80fccf50caca1af097b2f0830be52a
gh run list --repo minamorl/mozkey --workflow minamorl-linux-build.yml --limit 3
```

Leave `control_sha` empty. Both standard hosted architectures still build,
with maximum parallelism two and the existing finite timeouts. The stage job
name remains `dictionary`, but its actual generation is bypassed entirely.
No paid runner, cache service, additional account, or secret is introduced.
Do not send an intermediate ETA to Main TL. Send only the installation result
and usage, or a stop that requires the owner's action.

## Acceptance boundary

Run the public gate in TMP using `build_linux_gate_test.py` and the existing
actionlint binary. It retains the original 19 tests, eight EA2 compatibility
tests, pristine 17-file/hash checks, rejection probes, fork guard, actionlint,
shellcheck and recursive owned-code whitespace check. Reuse tests add
footer/POS/arch corruption checks, snapshot/hash/notices validation, overlay
source preservation, no-generation recipe, embedded-data matching and complete
snapshot/restore/package provenance handoff.

Local real extraction and byte encoding succeeded. New CI dispatch, actual
Bazel package-root resolution/native compilation, final ARM ZIP equality,
ELF dependency checks, Nix install and tablet input remain unverified here.
Root owns independent acceptance, Git delivery, CI dispatch and the separate
automated SSH/Nix installation lane. This worker's scope ends at the recipe.
