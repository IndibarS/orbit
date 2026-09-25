"""Verify release layout and ownership without installing on the host."""

import io
import subprocess
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from orbit_gtk.backend.apt_manager import AptManager
from tools.build_deb import APP_ID, build, stage


class PackagingTests(unittest.TestCase):
    def test_installed_layout_uses_only_scoped_helper(self):
        with patch(
            "orbit_gtk.backend.apt_manager.__file__",
            "/usr/lib/orbit-gtk/orbit_gtk/backend/apt_manager.py",
        ):
            self.assertEqual(
                AptManager.helper_command("install", "example:amd64"),
                ["pkexec", "/usr/libexec/orbit-gtk/orbit-helper", "install", "example:amd64"],
            )

    def test_archive_ownership_modes_and_policy_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = build(Path(directory))
            payload = subprocess.run(
                ["dpkg-deb", "--fsys-tarfile", str(archive)], check=True, capture_output=True
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(payload)) as package:
                entries = package.getmembers()
                self.assertTrue(entries)
                for entry in entries:
                    self.assertEqual((entry.uid, entry.gid), (0, 0))
                    self.assertEqual(entry.mode & 0o022, 0)
                    self.assertNotIn("__pycache__", entry.name)
                    self.assertNotIn(".venv", entry.name)
                helper = package.getmember("./usr/libexec/orbit-gtk/orbit-helper")
                self.assertEqual(helper.mode, 0o755)
                self.assertIn(b"#!/usr/bin/python3 -I", package.extractfile(helper).read())
                policy = ET.fromstring(
                    package.extractfile(f"./usr/share/polkit-1/actions/{APP_ID}.policy").read()
                )
                action = policy.find("action")
                self.assertEqual(action.findtext("defaults/allow_any"), "no")
                self.assertEqual(action.findtext("defaults/allow_inactive"), "no")
                self.assertEqual(action.findtext("defaults/allow_active"), "auth_admin")
                self.assertEqual(
                    action.find("annotate").text, "/usr/libexec/orbit-gtk/orbit-helper"
                )
                self.assertEqual(len(action.findall("annotate")), 1)

    def test_staging_does_not_include_reference_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            stage(Path(directory))
            runtime = Path(directory) / "usr/lib/orbit-gtk"
            self.assertEqual([path.name for path in runtime.iterdir()], ["orbit_gtk"])
            self.assertTrue((runtime / "orbit_gtk/backend/helper.py").is_file())
