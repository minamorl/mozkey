import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "minamorl"))

from corrected_apt import normalize_apt_sources


class AptSetupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.apt = Path(temp.name)
        (self.apt / "sources.list.d").mkdir()

    def write(self, name, contents):
        path = self.apt / name
        path.write_bytes(contents.encode())
        return path

    def test_deb822_preserves_signed_by_components_and_architectures(self):
        stanza = (
            "Types: deb deb-src\r\n"
            "URIs: http://us-east-1.ec2.archive.ubuntu.com/ubuntu/\r\n"
            "Suites: noble noble-updates\r\n noble-backports noble-security\r\n"
            "Components: main universe multiverse restricted\r\n"
            "Architectures: amd64\r\n"
            "Signed-By:\r\n -----BEGIN PGP PUBLIC KEY BLOCK-----\r\n .\r\n fixture\r\n"
            " -----END PGP PUBLIC KEY BLOCK-----\r\n"
        )
        untouched = (
            "Types: deb\r\nURIs: http://ports.ubuntu.com/ubuntu-ports\r\n"
            "Suites: noble noble-security\r\nComponents: main\r\nArchitectures: arm64\r\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\r\n\r\n"
            "Types: deb\r\nURIs: http://archive.ubuntu.com/ubuntu/\r\n"
            "Suites: noble\r\nArchitectures: arm64\r\nComponents: main\r\n\r\n"
            "Types: deb\r\nURIs: https://packages.example.invalid/ubuntu\r\n"
            "Suites: stable\r\nComponents: main\r\nSigned-By: /etc/apt/keyrings/vendor.gpg\r\n"
        )
        path = self.write("sources.list.d/ubuntu.sources", stanza + "\r\n" + untouched)
        other_format = self.write("sources.list.d/vendor.sources", untouched)
        expected = "\r\n".join(
            stanza.replace("http://us-east-1.ec2.archive.ubuntu.com/ubuntu/", uri).replace(
                "noble noble-updates\r\n noble-backports noble-security", suites,
            ) for uri, suites in (
                ("https://archive.ubuntu.com/ubuntu", "noble noble-updates noble-backports"),
                ("https://security.ubuntu.com/ubuntu", "noble-security"),
            )
        ) + "\r\n" + untouched
        normalize_apt_sources(self.apt, "amd64")
        self.assertEqual(expected.encode(), path.read_bytes())
        self.assertEqual(untouched.encode(), other_format.read_bytes())
        normalize_apt_sources(self.apt, "amd64")
        self.assertEqual(expected.encode(), path.read_bytes())

    def test_legacy_changes_only_matching_active_ubuntu_amd64_uris(self):
        archive = "deb [arch=amd64 signed-by=/keyring.gpg] http://azure.archive.ubuntu.com/ubuntu/ noble main # keep\n"
        security = "deb-src http://security.ubuntu.com/ubuntu noble-security universe\n"
        untouched = (
            "# deb http://us.archive.ubuntu.com/ubuntu noble main\n"
            "deb [arch=arm64] http://archive.ubuntu.com/ubuntu noble main\n"
            "deb http://ports.ubuntu.com/ubuntu-ports noble-security main\n"
            "deb http://archive.ubuntu.com.example.invalid/ubuntu noble main\n"
            "deb https://packages.example.invalid/ubuntu noble main\n"
        )
        root = self.write("sources.list", archive + untouched)
        security_file = self.write("sources.list.d/ubuntu-security.list", security)
        normalize_apt_sources(self.apt, "arm64")
        self.assertEqual((archive + untouched).encode(), root.read_bytes())
        self.assertEqual(security.encode(), security_file.read_bytes())
        normalize_apt_sources(self.apt, "amd64")
        self.assertEqual((archive.replace("http://azure.archive.ubuntu.com/ubuntu/", "https://archive.ubuntu.com/ubuntu")
                          + untouched).encode(), root.read_bytes())
        self.assertEqual(security.replace("http://", "https://").encode(), security_file.read_bytes())

    def test_workflow_passes_bounded_options_to_update_and_install(self):
        workflow = (ROOT / ".github/workflows/minamorl-linux-corrected.yml").read_text()
        install = workflow.split("- name: Install native build and ELF inspection tools\n", 1)[1]
        install = install.split("\n      - name:", 1)[0]
        self.assertEqual(8, int(re.search(r"timeout-minutes: (\d+)", install)[1]))
        options = shlex.split(re.search(r"apt_options=\(([^\n]+)\)", install)[1])
        self.assertEqual(["-o", "Acquire::http::Timeout=10", "-o", "Acquire::https::Timeout=10",
                          "-o", "Acquire::Retries=2"], options)
        self.assertIn('if [[ "$CORRECTED_SYSTEM" == x86_64-linux ]]; then', install)
        self.assertIn('sudo apt-get "${apt_options[@]}" update', install)
        self.assertIn('sudo apt-get "${apt_options[@]}" install -y --no-install-recommends', install)
