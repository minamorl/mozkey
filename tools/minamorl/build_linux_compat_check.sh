#!/usr/bin/env bash
# Current worker gate: deliberately no Git command, provisioning or CI dispatch.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: build_linux_compat_check.sh /path/to/actionlint' >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
"$1" .github/workflows/minamorl-linux-build.yml
echo 'actionlint: exit 0'
shellcheck tools/minamorl/build_linux.sh tools/minamorl/build_linux_check.sh tools/minamorl/build_linux_compat_check.sh
for script in tools/minamorl/build_linux.sh tools/minamorl/build_linux_check.sh tools/minamorl/build_linux_compat_check.sh; do
  bash -n "$script"
done
echo 'shellcheck and individual bash syntax: exit 0'
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_compat_test.py
echo 'Git-free compatibility fixtures: exit 0'
