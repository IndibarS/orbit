"""Distro discovery, archive isolation and incompatible mirror regressions."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from orbit_gtk.backend.helper import configure_mirrors
from orbit_gtk.backend.mirror_catalogues import parse_catalogue, validate_release
from orbit_gtk.backend.mirrors import (
    SourceSettings,
    UnsupportedMirrorDistribution,
    read_orbit_mirrors,
    render_orbit_source,
    replace_profile,
    source_profiles,
)


class MirrorProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "official.list"

    def profiles(self, distro, text):
        self.path.write_text(text)
        with patch("orbit_gtk.backend.mirrors.get_architectures", return_value=("amd64", "i386")):
            return source_profiles({"ID": distro}, [self.path], self.root / "lists")

    def test_devuan_catalogue_handles_adjacent_records_protocols_and_inactive(self):
        raw = (
            "FQDN: one.example\nBaseURL: one.example/devuan\nProtocols: HTTP | HTTPS\n"
            "Active: yes\nCountryCode: DE | FR\n"
            "FQDN: two.example\nBaseURL: two.example\nProtocols: HTTP\nActive: Yes\n"
            "FQDN: dead.example\nBaseURL: dead.example\nProtocols: HTTPS\nActive: out of date\n"
            "FQDN: ftp.example\nBaseURL: ftp.example\nProtocols: FTP\nActive: yes\n"
            "FQDN: invalid.example\nBaseURL: user@invalid.example\nProtocols: HTTPS\nActive: yes\n"
        )
        mirrors = parse_catalogue(raw, "devuan")
        self.assertEqual(
            [m.url for m in mirrors],
            ["https://one.example/devuan/merged", "http://two.example/merged"],
        )
        self.assertEqual(mirrors[0].country_code, "DE")
        self.assertEqual(
            parse_catalogue(raw, "devuan", archive="devuan")[0].url,
            "https://one.example/devuan/devuan",
        )
        with self.assertRaises(ValueError):
            parse_catalogue(raw, "devuan", archive="unknown")

    def test_devuan_layouts_and_debian_sources_remain_separate(self):
        profiles = self.profiles(
            "devuan",
            "deb [signed-by=/usr/share/keyrings/devuan-archive-keyring.gpg] https://deb.devuan.org/merged ceres main non-free-firmware\n"
            "deb https://pkgmaster.devuan.org/devuan ceres main\n"
            "deb https://deb.devuan.org/merged daedalus-security main\n"
            "deb https://deb.debian.org/debian sid main\n",
        )
        self.assertEqual(
            {p.repository for p in profiles},
            {"devuan:ceres:merged", "devuan:ceres:devuan", "debian:sid"},
        )
        merged = next(p for p in profiles if p.archive == "merged" and p.provider == "devuan")
        native = next(p for p in profiles if p.archive == "devuan")
        content = render_orbit_source(["https://one.example/merged"], merged)
        content = replace_profile(content, ["https://one.example/devuan"], native)
        content = replace_profile(content, [], merged)
        self.assertNotIn("one.example/merged", content)
        self.assertIn("one.example/devuan", content)

    def test_devuan_release_rejects_debian_and_wrong_layout(self):
        settings = SourceSettings("ceres", ("main",), provider="devuan", architectures=("amd64",))
        release = "Origin: Devuan\nLabel: Devuan\nSuite: unstable\nCodename: ceres\nArchitectures: amd64\nComponents: main\nSHA256:\n abc 1 main/Packages\n"
        validate_release(release.encode(), settings)
        for bad in (
            release.replace("Origin: Devuan", "Origin: Debian"),
            release.replace("Label: Devuan", "Label: Master"),
        ):
            with self.assertRaises(ValueError):
                validate_release(bad.encode(), settings)
        native = SourceSettings("unstable", ("main",), provider="devuan", archive="devuan")
        validate_release(release.replace("Label: Devuan", "Label: Master").encode(), native)

    def test_mint_archives_and_pockets_stay_separate(self):
        result = self.profiles(
            "linuxmint",
            "deb http://packages.linuxmint.com zena main upstream import backport\n"
            "deb http://archive.ubuntu.com/ubuntu noble main restricted universe multiverse\n"
            "deb http://archive.ubuntu.com/ubuntu noble-updates main restricted universe multiverse\n"
            "deb http://security.ubuntu.com/ubuntu noble-security main restricted universe multiverse\n",
        )
        self.assertEqual(
            {s.repository for s in result},
            {"linuxmint:zena", "ubuntu:noble", "ubuntu:noble-updates"},
        )
        self.assertEqual(result[0].components, ("main", "upstream", "import", "backport"))

    def test_lmde_uses_debian_base_not_ubuntu(self):
        result = self.profiles(
            "linuxmint",
            "deb http://packages.linuxmint.com faye main upstream import backport\n"
            "deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware\n",
        )
        self.assertEqual({s.provider for s in result}, {"linuxmint", "debian"})

    def test_deb822_preserves_restrictions_and_excludes_disabled_vendor_security(self):
        self.path = self.root / "ubuntu.sources"
        result = self.profiles(
            "ubuntu",
            "Types: deb deb-src\nURIs: https://archive.ubuntu.com/ubuntu\n"
            "Suites: noble noble-updates noble-security\nComponents: main universe\n"
            "Architectures: amd64\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n\n"
            "Types: deb\nURIs: https://archive.ubuntu.com/ubuntu\nSuites: jammy\nEnabled: no\n\n"
            "Types: deb\nURIs: https://ppa.example/ubuntu\nSuites: noble\nSigned-By: /keys/vendor.gpg\n",
        )
        self.assertEqual({p.suite for p in result}, {"noble", "noble-updates"})
        for profile in result:
            self.assertEqual(profile.architectures, ("amd64",))
            text = render_orbit_source(["https://mirror.example/ubuntu"], profile)
            self.assertIn("Architectures: amd64", text)
            self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg", text)
            self.assertIn("Components: main universe", text)

    def test_cached_origin_identifies_custom_mirror_but_rejects_vendor(self):
        import apt_pkg

        lists = self.root / "lists"
        lists.mkdir()
        for host, origin in (("mirror.example", "Ubuntu"), ("vendor.example", "Vendor")):
            uri = f"https://{host}/custom/dists/noble/InRelease"
            (lists / apt_pkg.uri_to_filename(uri)).write_text(f"Origin: {origin}\n")
        profiles = self.profiles(
            "pop",
            "deb https://mirror.example/custom noble main\n"
            "deb https://vendor.example/custom noble main restricted\n",
        )
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].components, ("main",))
        self.assertEqual(profiles[0].uris, ("https://mirror.example/custom",))

    def test_mixed_archives_are_identified_independently(self):
        profiles = self.profiles(
            "kali",
            "deb http://http.kali.org/kali kali-rolling main contrib non-free non-free-firmware\n"
            "deb http://deb.debian.org/debian sid main\n",
        )
        self.assertEqual([p.repository for p in profiles], ["kali:kali-rolling", "debian:sid"])
        self.assertEqual(
            self.profiles("unknown", "deb http://deb.debian.org/debian sid main\n")[0].repository,
            "debian:sid",
        )

    def test_unknown_archive_is_not_inferred_from_os_identity(self):
        for distro in ("debian", "ubuntu", "unknown"):
            with self.subTest(distro=distro), self.assertRaises(UnsupportedMirrorDistribution):
                self.profiles(distro, "deb https://vendor.example/archive noble main\n")

    def test_cached_metadata_overrides_host_and_rejects_ambiguity(self):
        import apt_pkg

        lists = self.root / "lists"
        lists.mkdir()
        release = lists / apt_pkg.uri_to_filename(
            "https://archive.ubuntu.com/ubuntu/dists/noble/InRelease"
        )
        for metadata in (
            "Origin: Vendor\n",
            "Suite: noble\n",
            "Origin: Ubuntu\nOrigin: Debian\n",
            "Origin: \n",
        ):
            release.write_text(metadata)
            with self.subTest(metadata=metadata), self.assertRaises(UnsupportedMirrorDistribution):
                self.profiles("unknown", "deb https://archive.ubuntu.com/ubuntu noble main\n")

    def test_recognized_origin_does_not_override_vendor_signing_key(self):
        import apt_pkg

        lists = self.root / "lists"
        lists.mkdir()
        (lists / apt_pkg.uri_to_filename("https://vendor.example/dists/noble/Release")).write_text(
            "Origin: Ubuntu\n"
        )
        with self.assertRaises(UnsupportedMirrorDistribution):
            self.profiles(
                "unknown", "deb [signed-by=/keys/vendor.gpg] https://vendor.example noble main\n"
            )

    def test_missing_os_release_does_not_block_offline_discovery(self):
        self.path.write_text("deb https://deb.debian.org/debian sid main\n")
        with patch(
            "orbit_gtk.backend.mirrors.platform.freedesktop_os_release", side_effect=OSError
        ):
            profiles = source_profiles(paths=[self.path], lists_dir=self.root / "missing")
        self.assertEqual(profiles[0].repository, "debian:sid")

    def test_unreadable_metadata_is_skipped_without_crashing(self):
        import apt_pkg

        lists = self.root / "lists"
        lists.mkdir()
        (
            lists
            / apt_pkg.uri_to_filename("https://archive.ubuntu.com/ubuntu/dists/noble/InRelease")
        ).mkdir()
        with self.assertRaises(UnsupportedMirrorDistribution):
            self.profiles("unknown", "deb https://archive.ubuntu.com/ubuntu noble main\n")

    def test_duplicate_archive_uris_are_collected_without_losing_options(self):
        profiles = self.profiles(
            "ubuntu",
            "deb http://archive.ubuntu.com/ubuntu noble main\n"
            "deb http://us.archive.ubuntu.com/ubuntu noble main\n"
            "deb [arch+=arm64] http://archive.ubuntu.com/ubuntu noble main\n",
        )
        self.assertEqual(len(profiles), 1)
        self.assertEqual(
            set(profiles[0].uris),
            {"http://archive.ubuntu.com/ubuntu", "http://us.archive.ubuntu.com/ubuntu"},
        )
        self.assertEqual(profiles[0].architectures, ("amd64", "i386"))

    def test_compatible_component_and_source_package_settings_merge(self):
        self.path = self.root / "debian.sources"
        key = "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
        result = self.profiles(
            "debian",
            "Types: deb deb-src\nURIs: https://deb.debian.org/debian\nSuites: sid\n"
            "Components: main contrib non-free non-free-firmware\n" + key + "\n"
            "Types: deb\nURIs: https://ftp.debian.org/debian\nSuites: sid\n"
            "Components: main\n" + key,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].components, ("main", "contrib", "non-free", "non-free-firmware"))
        self.assertTrue(result[0].source_packages)
        self.assertEqual(len(result[0].uris), 2)

    def test_conflicting_sources_fail_instead_of_merging_permissions(self):
        for options in (
            "arch=amd64",
            "signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg",
        ):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "Conflicting"):
                self.profiles(
                    "ubuntu",
                    "deb http://archive.ubuntu.com/ubuntu noble main\n"
                    f"deb [{options}] http://archive.ubuntu.com/ubuntu noble main universe\n",
                )

    def test_catalogues_exclude_iso_and_navigation_urls(self):
        mint = "<table><td>https://iso.example/mint</td></table><h2>Repository mirrors</h2><table><td>https://packages.example/mint/</td></table><table><td>https://sponsor.example/</td></table>"
        self.assertEqual(
            [m.url for m in parse_catalogue(mint, "linuxmint")], ["https://packages.example/mint"]
        )
        ubuntu = '<a href="https://navigation.example">https</a><table><td><a href="http://archive.example/ubuntu">http</a><a href="https://archive.example/ubuntu">https</a></td></table>'
        self.assertEqual(
            [m.url for m in parse_catalogue(ubuntu, "ubuntu")], ["https://archive.example/ubuntu"]
        )
        kali = '<table><td><a href="https://archive.example/kali">https://archive.example/kali</a><a href="https://logo.example">Sponsor</a></td></table>'
        self.assertEqual(
            [m.url for m in parse_catalogue(kali, "kali")], ["https://archive.example/kali"]
        )

    def test_release_checks_provider_suite_components_architecture_and_expiry(self):
        settings = SourceSettings(
            "noble", ("main", "universe"), provider="ubuntu", architectures=("amd64", "i386")
        )
        text = "Origin: Ubuntu\nSuite: noble\nCodename: noble\nComponents: main universe\nArchitectures: amd64 i386\nSHA256:\n abc 1 main/binary-amd64/Packages\n"
        validate_release(text.encode(), settings)
        for bad in (
            text.replace("Ubuntu", "Debian"),
            text.replace("noble", "jammy"),
            text.replace("main universe", "main"),
            text.replace("amd64 i386", "amd64"),
            text.replace("SHA256", "SHA1"),
            text + "Valid-Until: Tue, 01 Jan 2000 00:00:00 UTC\n",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_release(bad.encode(), settings)

    def test_changes_and_clear_are_scoped_and_legacy_debian_migrates(self):
        mint = SourceSettings("zena", ("main",), provider="linuxmint")
        ubuntu = SourceSettings("noble", ("main",), provider="ubuntu")
        content = render_orbit_source(["https://mint.example"], mint)
        content = replace_profile(content, ["https://ubuntu.example"], ubuntu)
        content = replace_profile(content, [], mint)
        self.assertNotIn("mint.example", content)
        self.assertIn("ubuntu.example", content)
        self.path.write_text(content)
        self.assertEqual(read_orbit_mirrors(self.path, ubuntu), ["https://ubuntu.example"])
        self.assertEqual(read_orbit_mirrors(self.path, mint), [])
        legacy = "Types: deb\nURIs: https://old.example/debian\nSuites: sid\nComponents: main\n"
        self.assertNotIn(
            "old.example", replace_profile(legacy, [], SourceSettings("sid", ("main",)))
        )

    def test_helper_rechecks_and_never_writes_failed_or_wrong_suite_selection(self):
        settings = SourceSettings("noble", ("main",), provider="ubuntu")
        self.path.write_text("existing content")
        with (
            patch("orbit_gtk.backend.helper.ORBIT_SOURCES_PATH", self.path),
            patch("orbit_gtk.backend.helper.source_settings", return_value=settings),
            patch(
                "orbit_gtk.backend.helper.validate_mirror", side_effect=ValueError("Wrong origin")
            ) as probe,
        ):
            with self.assertRaisesRegex(ValueError, "Wrong origin"):
                configure_mirrors(["https://mirror.example"], "noble", Mock(), "ubuntu:noble")
            self.assertEqual(self.path.read_text(), "existing content")
            with self.assertRaisesRegex(ValueError, "settings changed"):
                configure_mirrors(["https://mirror.example"], "jammy", Mock(), "ubuntu:noble")
            self.assertEqual(probe.call_count, 1)

    def test_helper_save_and_clear_preserve_other_archives(self):
        mint = SourceSettings("zena", ("main",), provider="linuxmint")
        ubuntu = SourceSettings("noble", ("main",), provider="ubuntu")
        self.path.write_text(render_orbit_source(["https://mint.example"], mint))
        with (
            patch("orbit_gtk.backend.helper.ORBIT_SOURCES_PATH", self.path),
            patch("orbit_gtk.backend.helper.source_settings", return_value=ubuntu),
            patch("orbit_gtk.backend.helper.validate_mirror"),
            patch("orbit_gtk.backend.helper.existing_mirror_urls", return_value=set()),
        ):
            configure_mirrors(["https://ubuntu.example"], "noble", Mock(), "ubuntu:noble")
            self.assertIn("ubuntu.example", self.path.read_text())
            configure_mirrors(None, None, Mock(), "ubuntu:noble")
            self.assertIn("mint.example", self.path.read_text())
            self.assertNotIn("ubuntu.example", self.path.read_text())
