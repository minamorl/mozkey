"""Check the fixed public corrected commit in a disposable clean checkout."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "minamorl"))

from corrected_build import worktree_state
from corrected_inputs import FORK_URL, measure_target, verify_fork_checkout, verify_ibus_build


SOURCE = "45682407ee02ce1efbdec0f3a425b31fd3d0bd5a"
TREE = "8d7172de14c73f01167029dc9e6623a2be0893b3"


def main():
    with tempfile.TemporaryDirectory(prefix="corrected-frozen-source-") as scratch:
        target = Path(scratch).resolve() / "target"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "remote", "set-url", "origin", FORK_URL], check=True)
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "--prune", "origin"], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", "--quiet", "--detach", SOURCE], check=True)
        verify_fork_checkout(target, SOURCE)
        measured, package = measure_target(target, SOURCE)
        if measured["tree_sha"] != TREE:
            raise ValueError("Unexpected tree for the fixed corrected source")
        verify_ibus_build(target, measured)
        print("accepted source_sha:", measured["source_sha"])
        print("tree_sha:", measured["tree_sha"])
        print("changed_paths:", json.dumps(measured["changed_paths"]))
        print("target/format digest counts:", len(measured["files_sha256"]), len(package["source_files_sha256"]))
        print("tracked checkout SHA-256:", worktree_state(target, SOURCE))


if __name__ == "__main__":
    main()
