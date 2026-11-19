#!/usr/bin/env bash
# Local gates only. No build, provisioning, repository mutation or CI dispatch.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: build_linux_check.sh /path/to/actionlint' >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
"$1" -version
"$1" .github/workflows/minamorl-linux-build.yml
echo 'actionlint: exit 0'
shellcheck tools/minamorl/build_linux.sh tools/minamorl/build_linux_check.sh
for script in tools/minamorl/build_linux.sh tools/minamorl/build_linux_check.sh; do
  bash -n "$script"
done
echo 'shellcheck and bash -n: exit 0'
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_test.py
echo 'provenance recipe tests: exit 0'
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_compat_test.py
echo 'unpatched PC revision compatibility fixtures: exit 0'
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_dataset_test.py
echo 'existing PC dataset reuse fixtures: exit 0'
for guard_case in wrong-repository not-actions; do
  if [[ $guard_case == wrong-repository ]]; then
    actions=true
    repository=example/not-the-fork
  else
    actions=false
    repository=minamorl/mozkey
  fi
  if report=$(GITHUB_ACTIONS=$actions GITHUB_REPOSITORY=$repository \
      bash tools/minamorl/build_linux.sh package . must-not-exist missing-snapshot 2>&1); then
    echo "ERROR: $guard_case build accepted" >&2
    exit 1
  else
    status=$?
  fi
  [[ $status == 1 && $report == 'ERROR: fork Actions runner required' ]] || {
    echo "ERROR: $guard_case returned an unrelated failure: exit $status: $report" >&2
    exit 1
  }
  echo "$guard_case: exit $status; $report"
done
echo 'fork and Actions guard assertions: exit 0'
PYTHONDONTWRITEBYTECODE=1 python3 tools/minamorl/build_linux_primary.py --check-whitespace
