# Corrected producer R3 pre-dispatch verification

**PASS for the requested code gate.** The prior stdout diagnostic failure
was reproduced as a child stdout-only failure and is now reported. The new
fixed source passed the producer guard in a clean disposable checkout with
refs fetched from the public own fork.

```text
$ shasum -a 256 pins/domains/mozkey-corrected-ci.spec pins/domains/mozkey-fork-distribution.spec
exit 0
dd9b706c18adfbcd228a3e9a67a3af6f0a1f202a2a4472198616b17213f39b03  pins/domains/mozkey-corrected-ci.spec
ad4d0904080301ad853b8ca2e39016820cea0d6fad54a45238229742e92892d2  pins/domains/mozkey-fork-distribution.spec

$ git diff --exit-code 2221a65432eda9c71c79ff08e8008dc218720fab --
exit 0, stdout empty
$ git ls-remote --exit-code https://github.com/minamorl/mozkey refs/heads/fix/ibus-property-output-ea2-20261119
exit 0
45682407ee02ce1efbdec0f3a425b31fd3d0bd5a	refs/heads/fix/ibus-property-output-ea2-20261119

$ python3 -B tests/minamorl_corrected/check_frozen_source.py
exit 0
accepted source_sha: 45682407ee02ce1efbdec0f3a425b31fd3d0bd5a
tree_sha: 8d7172de14c73f01167029dc9e6623a2be0893b3
changed_paths: ["src/unix/ibus/BUILD.bazel", "src/unix/ibus/mozc_engine.cc", "src/unix/ibus/property_handler.cc", "src/unix/ibus/property_handler.h", "src/unix/ibus/property_handler_test.cc"]
target/format digest counts: 5 9
tracked checkout SHA-256: 61f03acd0e993df2f4ce767bf19c54c70b1463d01ff2826deefdbe541cf0cbf7

$ python3 -B -m unittest discover -s tests/minamorl_corrected -v
exit 0
----------------------------------------------------------------------
Ran 23 tests in 2.437s

OK
```

The EA2 BUILD diff, read directly from the two Git objects, is confined
to `mozc_cc_test(name = "ibus_mozc_test")`. Within the existing Linux selector,
the only new source is `property_handler_test.cc`. Added dependencies
are `:ibus_header`, `:ibus_property_handler`, `:ibus_utils`,
`:ibus_wrapper`, `//base:file_util`, `//base:system_util`, `//client`
and `//testing:mozctest`. Existing entries remain in order.

## Independent probe

```text
$ PYTHONPATH=tools/minamorl python3 -B "$VERIFIER_R3_SCRATCH/r3_probe.py" "$VERIFIER_R3_SCRATCH"
exit 0
stdout-only: CLI exit 1, child exit 17, tests/source/tree/command bound, stdout_tail=['test stdout assertion failure'], stderr_tail=[]
mixed: CLI exit 1, child exit 23, stdout lines 16, stderr lines 6, longest line 480 chars, redaction/prefix-negative checks PASS
opaque subprocess: CLI exit 1, child exit 23, stdout absent
success: CLI exit 0, child exit 0, stderr empty, child stdout/stderr absent from event
EA2 ancestry exit 0; Git diff equals all five allowed paths
59acd943a0d15254345f3e176f42786af2b4fba83b1657341cf56e017a7db19a src/.bazeliskrc
08ac59def69d2100356fcce7f74a32775bd4c142378689338182f7b5e48999af src/config.bzl
a0cc21085ed84db13fffdb95e7ce4be88395f71293946feeab95b8fabddc3b1a src/data/dictionary_koyasi/BUILD.bazel
dfbe4196c50c956c7abc33c902a80f0babaaad347d393c25c608a9f8cec3956a src/data/dictionary_oss/BUILD.bazel
5abdaa43194aa97593e437dd54a59c1ff3e1937894e980ca3439439f64a65962 src/unix/BUILD.bazel
c0a349680b64aafa4f1b08d296dfb16b4110e52384ce7f35334c9850bb536a91 src/unix/build_icons.py
fa05a3838125a479375e6d11d72bbf8767d916b7f05e2ae1ca57ad8372aaaa19 src/unix/build_package.py
00779e59b9bc657926c819a490ef9e8a7b342b91f33b66428370ec646bd71a6a src/unix/ibus/BUILD.bazel
e8fabab2bc690830b26b7cab28e0a6fefd3c5ef6ad05fd6714088158d9ff2138 src/unix/ibus/gen_mozc_xml.py
fa0f05bc1569339772b4ca1714405a948713ac745048a96893716b6943b30224 src/unix/ibus/mozc_engine.cc
580391b3b34a722905dfa6158225c65c548c39dc89687da190573559887ef223 src/unix/ibus/property_handler.cc
99fb3899c780aeac5e3d4cef6f49c83ee20db1a3229b9e025b9adf40b12e845d src/unix/ibus/property_handler.h
a626241afa943bd2629e290cb360ab82bf35dd68a9d4d820f76830b6cc537290 src/unix/ibus/property_handler_test.cc
```

The diagnostic probe used real child processes under the producer's
`run_command`, `run_phase` and CLI exception logger. Harness stubs isolated
Git, dataset and overlay verification from those child processes. The
separate clean-source check used real Git bytes and the full source guard.
The hash listing reports the union of the five target and nine format paths.
The independently read eight non-IBus-BUILD format inputs matched EA2.

`corrected_build.py:70–97,249–255` carries phase, source, tree, argv and
child exit code into the failure JSON. `corrected_diagnostics.py:26–58`
removes credential-bearing lines, strips URL userinfo and queries,
and bounds failure output. The child verified that an unlisted parent
environment value did not reach it. Successful stages kept subprocess
stdout and stderr out of the emitted event. The fixture did not invoke Bazel.

## Unverified

Both corrected native CI jobs, their actual unit tests and runtime/ABI,
and upload/download of a corrected artifact are the next gate. No CI dispatch,
owner operation or Git delivery was performed. R2 package and wire probes were
outside this delta and were carried forward from their prior measured record.
