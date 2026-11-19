"""Exercise the actual pinned PC archive and the public overlay interface on demand."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "minamorl"))

from corrected_archive import compare_inventory
from corrected_inputs import BASE_SHA, DICTIONARY, source_fingerprint, verify_dataset, verify_pc_zip
from corrected_recipe import (
    json_object, measure_notices, public_dataset_cli, recipe_members, verify_embedded_dataset, verify_overlay,
)


def main():
    with tempfile.TemporaryDirectory(prefix="corrected-fixed-pc-") as directory:
        scratch = Path(directory).resolve()
        ea2, pc, data, overlay = (scratch / name for name in ("ea2", "pc.zip", "data", "dataset-overlay"))
        fork = scratch / "fork"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(fork)], check=True)
        subprocess.run(["git", "-C", str(fork), "worktree", "add", "--quiet", "--detach", str(ea2), BASE_SHA], check=True)
        print("EA2 tracked checkout fingerprint:", source_fingerprint(ea2, BASE_SHA), flush=True)
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
                        "--max-time", "90", "--output", str(pc), DICTIONARY["pc_zip_url"]], check=True)
        verify_pc_zip(pc)
        public_dataset_cli(ROOT, pc, data)
        verify_dataset(data)
        print("PC archive, dataset, POS and receipt SHA-256 verified", flush=True)
        embedded = verify_embedded_dataset(ROOT, pc, 62)
        print("PC embedded dataset and POS verified for ELF machine:", embedded["elf_machine"], flush=True)
        record = json_object(public_dataset_cli(ROOT, "--overlay", ea2, data, overlay))
        digests = verify_overlay(ea2, ea2, overlay, record)
        print("overlay public receipt verified:", ", ".join(sorted(digests)), flush=True)
        with ZipFile(pc) as baseline, ZipFile(pc) as reference:
            members, elfs, assets = compare_inventory(baseline, reference, 62)
            print("PC package regular/ELF/non-ELF member counts:", len(members), len(elfs), len(assets))
            print("PC ELF PT_LOAD counts:", sorted(len(item["pt_load"]) for item in elfs.values()))
        print("Designated recipe members verified:", ", ".join(sorted(recipe_members(ea2, overlay, digests))))
        os.environ["GITHUB_SHA"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        notices = measure_notices(ROOT, ea2, data, scratch / "snapshot")
        print("EA2 snapshot notices verified:", len(notices))


if __name__ == "__main__":
    main()
