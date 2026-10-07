"""Command parsing works without GTK, shell expansion or an elevated process."""

import contextlib
import io
import unittest

from orbit_gtk.cli import parse_command
from orbit_gtk.main import main


class CommandTests(unittest.TestCase):
    def test_nala_style_routes(self):
        for command in (
            "update",
            "upgrade",
            "full-upgrade",
            "autoremove",
            "clean",
            "history",
            "fetch",
        ):
            self.assertEqual(parse_command([command]).command, command)
        self.assertEqual(parse_command(["dist-upgrade"]).command, "full-upgrade")
        self.assertEqual(parse_command(["info", "bash"]).command, "show")
        self.assertTrue(parse_command(["list", "--installed"]).installed)
        self.assertEqual(parse_command(["search", "text", "editor"]).query, ["text", "editor"])

    def test_local_path_uses_callers_directory(self):
        request = parse_command(["install", "./a package.deb"], "/tmp/caller")
        self.assertEqual(request.command, "install-local")
        self.assertEqual(request.path, "/tmp/caller/a package.deb")

    def test_invalid_or_unattended_requests_are_rejected(self):
        for arguments in (
            ["install"],
            ["install", "-y", "bash"],
            ["install-local", "a.txt"],
            ["list", "--installed", "--upgradable"],
        ):
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parse_command(arguments)

    def test_local_batch_can_include_repository_packages(self):
        request = parse_command(["install", "a.deb", "b.deb", "bash"], "/tmp/caller")
        self.assertEqual(request.command, "install-batch")
        self.assertEqual(request.paths, ["/tmp/caller/a.deb", "/tmp/caller/b.deb"])
        self.assertEqual(request.packages, ["bash"])

    def test_help_and_version_need_no_display(self):
        for option in ("--help", "--version"):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["orbit-gtk", option]), 0)
            self.assertIn("Orbit", output.getvalue())
