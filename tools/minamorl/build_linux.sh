#!/usr/bin/env bash
# Run only on disposable standard GitHub Linux runners, never on a desktop/guest.
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
[[ $# -ge 3 ]] || die 'usage: build_linux.sh dictionary SOURCE OUTPUT | package SOURCE OUTPUT SNAPSHOT'
mode=$1
source_root=$(cd "$2" && pwd)
output=$3
recipe_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == minamorl/mozkey ]] || die 'fork Actions runner required'
[[ $(uname -s) == Linux ]] || die 'native Linux required'
: "${RUNNER_TEMP:?}"
export TMPDIR="$RUNNER_TEMP"
[[ ! -e $output ]] || die 'output must be a new directory'
[[ -z $(git -C "$source_root" status --porcelain --untracked-files=normal) ]] || die 'source checkout must be clean'
export ANDROID_NDK_HOME=''

case "$mode" in
  dictionary)
    [[ $# == 3 ]] || die 'unexpected dictionary arguments'
    profile=$(python3 "$recipe_dir/build_linux_metadata.py" profile "$source_root")
    [[ $profile == upstream-ea2e9f5c-mozc ]] || die 'PC dataset reuse is pinned to unchanged EA2'
    # Existing PC bytes only: no PowerShell/importer/data builder fallback.
    curl --fail --location --silent --show-error --max-time 180 --retry 2 \
      'https://github.com/minamorl/mozkey/releases/download/ci-pc-ea2e9f5c-data/mozkey-ea2e9f5c-linux.zip' \
      -o "$RUNNER_TEMP/pc-linux.zip"
    python3 "$recipe_dir/build_linux_dataset.py" "$RUNNER_TEMP/pc-linux.zip" "$RUNNER_TEMP/pc-data"
    git -C "$source_root" diff --exit-code HEAD --
    python3 "$recipe_dir/build_linux_metadata.py" pc-snapshot "$source_root" "$output" "$RUNNER_TEMP/pc-data"
    ;;
  package)
    [[ $# == 4 ]] || die 'snapshot directory required'
    : "${EXPECTED_SOURCE_SHA:?}" "${EXPECTED_ARCH:?}" "${EXPECTED_DICTIONARY_SHA256:?}"
    [[ $(git -C "$source_root" rev-parse HEAD) == "$EXPECTED_SOURCE_SHA" ]] || die 'source SHA mismatch'
    [[ $(uname -m) == "$EXPECTED_ARCH" ]] || die 'native architecture mismatch'
    extra_args=()
    if [[ $EXPECTED_SOURCE_SHA == ea2e9f5cfb80fccf50caca1af097b2f0830be52a ]]; then
      [[ -f $4/dataset.json ]] || die 'EA2 body build requires existing PC dataset; regeneration is forbidden'
      extra_args+=("--package_path=$RUNNER_TEMP/dataset-overlay:%workspace%" --strip=never)
    fi
    python3 "$recipe_dir/build_linux_metadata.py" restore "$source_root" "$4" "$EXPECTED_DICTIONARY_SHA256"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends libibus-1.0-dev qt6-base-dev build-essential pkg-config python-is-python3
    case "$EXPECTED_ARCH" in
      x86_64)
        asset_arch=amd64
        bazelisk_sha=e1508323f347ad1465a887bc5d2bfb91cffc232d11e8e997b623227c6b32fb76
        export BAZELISK_VERIFY_SHA256=422e7a1690b76d7e615c29091d3aca28d0bd3a93fe3c93cbefb8f72d774926d5
        ;;
      aarch64)
        asset_arch=arm64
        bazelisk_sha=bb608519a440d45d10304eb684a73a2b6bb7699c5b0e5434361661b25f113a5d
        export BAZELISK_VERIFY_SHA256=e9fc2c7b0027619058acae81c063e5ced683cb0f1598e9373b4f752820c03adb
        ;;
      *) die 'unsupported architecture' ;;
    esac
    # Refuse a source Bazel version change until both native checksums are reviewed.
    [[ $(sed -n 's/^USE_BAZEL_VERSION=//p' "$source_root/src/.bazeliskrc") == 9.0.2 ]] || die 'review Bazel version and checksums'
    export USE_BAZEL_VERSION=9.0.2
    export BAZELISK_HOME="$RUNNER_TEMP/bazelisk"
    bazelisk="$RUNNER_TEMP/bazelisk-v1.27.0"
    curl --fail --location --silent --show-error --max-time 180 --retry 2 \
      "https://github.com/bazelbuild/bazelisk/releases/download/v1.27.0/bazelisk-linux-$asset_arch" -o "$bazelisk"
    echo "$bazelisk_sha  $bazelisk" | sha256sum --check --strict
    chmod 755 "$bazelisk"
    cd "$source_root/src"
    timeout 80m "$bazelisk" --batch --nosystem_rc --nohome_rc \
      --output_user_root="$RUNNER_TEMP/bazel-output" build package --config release_build --jobs=2 "${extra_args[@]}"
    # Detect source/config drift before declaring provenance.
    git -C "$source_root" diff --exit-code HEAD --
    python3 "$recipe_dir/build_linux_metadata.py" package "$source_root" "$output" "$4" "$EXPECTED_ARCH"
    ;;
  *) die 'unknown mode' ;;
esac
