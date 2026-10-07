"""Repository suite identity without source writes or host-dependent fixtures."""

import tempfile
import unittest
from pathlib import Path

from orbit_gtk.backend.system_identity import system_identity


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.paths = {
            "source_list": self.root / "sources.list",
            "source_dir": self.root,
            "lists_dir": self.root / "lists",
        }
        self.identity = {
            "ID": "debian",
            "NAME": "Debian GNU/Linux",
            "PRETTY_NAME": "Debian GNU/Linux forky/sid",
            "VERSION_CODENAME": "forky",
        }

    def detect(self):
        return system_identity(self.identity, **self.paths)

    def test_sid_overrides_ambiguous_os_codename(self):
        source = self.root / "debian.sources"
        text = "Types: deb\nURIs: https://deb.debian.org/debian/\nSuites: sid\nComponents: main\n"
        source.write_text(text)
        self.assertEqual(self.detect()["suite"], "Sid (unstable)")
        self.assertIn("Sid (unstable)", self.detect()["distro"])
        self.assertEqual(source.read_text(), text)

    def test_current_debian_pgp_keyring_recognizes_sid(self):
        (self.root / "debian.sources").write_text(
            "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: sid\n"
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp\n"
        )
        (self.root / "vendor.sources").write_text(
            "Types: deb\nURIs: https://vendor.example/debian\nSuites: stable\n"
            "Signed-By: /keys/vendor.pgp\n"
        )
        self.assertEqual(self.detect()["suite"], "Sid (unstable)")

    def test_legacy_aliases_and_managed_sources(self):
        (self.root / "sources.list").write_text(
            "deb [arch=amd64] https://deb.debian.org/debian unstable main\n"
        )
        (self.root / "orbit-mirrors.sources").write_text(
            "Types: deb\nURIs: https://deb.debian.org/debian\nSuites: sid\n"
        )
        self.assertEqual(self.detect()["suite"], "Sid (unstable)")

    def test_ignore_disabled_source_only_vendor_and_supplemental_suites(self):
        (self.root / "feeds.sources").write_text(
            "Types: deb\nURIs: https://deb.debian.org/debian\nSuites: forky\n\nTypes: deb\nURIs: https://deb.debian.org/debian\nSuites: sid\nEnabled: no\n\nTypes: deb-src\nURIs: https://deb.debian.org/debian\nSuites: sid\n\nTypes: deb\nURIs: https://vendor.example/linux/debian\nSuites: bookworm\n\nTypes: deb\nURIs: https://deb.debian.org/debian\nSuites: forky-updates forky-backports\n"
        )
        self.assertEqual(self.detect()["suite"], "forky")

    def test_vendor_using_debian_path_does_not_override_sid(self):
        (self.root / "sources.list").write_text(
            "deb https://deb.debian.org/debian sid main\ndeb [signed-by=/keys/vendor.gpg] https://vendor.example/debian stable main\n"
        )
        (self.root / "vendor.sources").write_text(
            "Types: deb\nURIs: https://vendor.example/debian/\nSuites: stable\nSigned-By: /keys/vendor.gpg\n"
        )
        self.assertEqual(self.detect()["suite"], "Sid (unstable)")

    def test_unsigned_vendor_origin_is_not_a_debian_suite(self):
        import apt_pkg

        lists = self.root / "lists"
        lists.mkdir()
        uri = "https://vendor.example/debian/dists/stable/Release"
        (lists / apt_pkg.uri_to_filename(uri)).write_text("Origin: Vendor\n")
        (self.root / "sources.list").write_text(
            "deb https://deb.debian.org/debian sid main\ndeb [trusted=yes] https://vendor.example/debian stable main\n"
        )
        self.assertEqual(self.detect()["suite"], "Sid (unstable)")

    def test_mixed_suites_not_silently_labelled_sid(self):
        (self.root / "sources.list").write_text(
            "deb https://deb.debian.org/debian forky main\ndeb https://deb.debian.org/debian sid main\n"
        )
        self.assertEqual(self.detect()["suite"], "Mixed Debian suites: forky, sid")

    def test_no_sources_does_not_guess_sid_from_pretty_name(self):
        self.assertEqual(self.detect()["suite"], "OS codename: forky")
        self.assertEqual(self.detect()["distro"], self.identity["PRETTY_NAME"])

    def test_derivative_keeps_its_own_identity(self):
        (self.root / "sources.list").write_text("deb https://deb.debian.org/debian sid main\n")
        self.identity.update(
            ID="linuxmint",
            NAME="Linux Mint",
            PRETTY_NAME="Linux Mint Test",
            VERSION_CODENAME="test",
        )
        self.assertEqual(self.detect()["distro"], "Linux Mint Test")
        self.assertEqual(self.detect()["suite"], "OS codename: test")
