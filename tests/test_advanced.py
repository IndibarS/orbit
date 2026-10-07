"""Transaction options and replay guards, without changing host package state."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import apt_pkg
from test_backend import cache_with, package

from orbit_gtk.backend.journal import Journal, read_records
from orbit_gtk.backend.models import HistoryTransaction, PackageInfo
from orbit_gtk.backend.replay import replay_requests
from orbit_gtk.backend.transaction_options import validate_options
from orbit_gtk.backend.transactions import TransactionRejected, execute, mark_changes
from orbit_gtk.cli import parse_command


class AdvancedTests(unittest.TestCase):
    def test_residual_config_purge(self):
        pkg = package(installed=False, delete=True)
        pkg._pkg.current_state = apt_pkg.CURSTATE_CONFIG_FILES
        plan = mark_changes(cache_with(pkg), "purge", ["example"])
        self.assertEqual(plan[0]["action"], "purge")
        pkg.mark_delete.assert_called_once_with(auto_fix=True, purge=True)

    def test_requested_downgrade_only(self):
        pkg = package(downgrade=True)
        pkg.versions = {"0.5": SimpleNamespace(version="0.5", uris=[])}
        plan = mark_changes(
            cache_with(pkg), "install", ["example"], {"versions": {"example": "0.5"}}
        )
        self.assertEqual(plan[0]["action"], "downgrade")
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(pkg), "install", ["example"])

    def test_autopurge_is_restricted_to_unused(self):
        pkg = package(delete=True)
        pkg.is_auto_removable = True
        cache = cache_with(pkg)
        cache.__iter__.return_value = iter([pkg])
        self.assertEqual(mark_changes(cache, "autopurge", [])[0]["action"], "purge")

    def test_download_only_never_commits(self):
        pkg = package()
        cache = cache_with(pkg)
        with (
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
        ):
            self.assertTrue(
                execute(
                    "install",
                    ["example"],
                    lambda *_a, **_k: None,
                    lambda _: True,
                    {"download_only": True},
                )
            )
        cache.fetch_archives.assert_called_once()
        cache.commit.assert_not_called()

    def test_batch_rejects_changed_expected_version(self):
        with self.assertRaisesRegex(TransactionRejected, "changed"):
            mark_changes(
                cache_with(package()),
                "batch",
                [],
                {"requests": [{"action": "remove", "name": "example", "expected": "0.9"}]},
            )

    def test_unknown_option_rejected(self):
        with self.assertRaises(ValueError):
            validate_options({"allow_unauthenticated": True})

    def test_cli_versions_and_options(self):
        request = parse_command(
            ["install", "example=1", "--download-only", "--no-install-recommends"]
        )
        self.assertEqual(request.packages, ["example"])
        self.assertEqual(request.options["versions"], {"example": "1"})
        self.assertFalse(request.options["recommends"])

    def test_journal_survives_reload(self):
        with tempfile.TemporaryDirectory() as root:
            journal = Journal("install", Path(root))
            journal.event("plan", {"changes": [{"name": "example", "action": "install"}]})
            journal.event("complete", {})
            self.assertEqual(read_records(Path(root))[0]["status"], "Completed")
            self.assertEqual(len(list(Path(root).glob("*.json"))), 1)

    def test_undo_requires_recorded_versions(self):
        transaction = HistoryTransaction(
            "1",
            "date",
            "user",
            "cmd",
            "upgrade",
            1,
            status="Completed",
            upgraded_pkgs=[PackageInfo("example", installed_version="1", latest_version="2")],
        )
        self.assertEqual(
            replay_requests(transaction, True)[0],
            {"name": "example", "action": "install", "version": "1", "expected": "2"},
        )
        transaction.status = "Failed"
        with self.assertRaises(ValueError):
            replay_requests(transaction, True)


class ConfigurationTests(unittest.TestCase):
    def test_passthrough_protocol(self):
        from orbit_gtk.backend.debconf import DebconfBridge

        class Stream:
            def __init__(self):
                self.lines = iter(
                    [
                        "DATA test type boolean\n",
                        "DATA test description Enable feature?\n",
                        "SET test false\n",
                        "INPUT high test\n",
                        "GO\n",
                        "GET test\n",
                        "STOP\n",
                    ]
                )
                self.output = []

            def readline(self, _):
                return next(self.lines, "")

            def write(self, value):
                self.output.append(value)

            def flush(self):
                pass

        stream = Stream()
        asked = []
        bridge = DebconfBridge(lambda question: asked.append(question) or "true")
        bridge.serve(stream)
        self.assertEqual(asked[0]["kind"], "boolean")
        self.assertIn("0 true\n", stream.output)

    def test_ubuntu_structured_country_catalogue(self):
        from orbit_gtk.backend.mirror_catalogues import parse_catalogue

        result = parse_catalogue(
            '<rss xmlns:m="https://launchpad.net/"><channel><item><link>https://example.test/ubuntu</link><m:location><m:countrycode>DE</m:countrycode></m:location></item></channel></rss>',
            "ubuntu",
        )
        self.assertEqual(result[0].country_code, "DE")

    def test_regex_search_timeout_is_bounded(self):
        import regex

        with self.assertRaises(TimeoutError):
            regex.compile("(a+)+$").search("a" * 100000 + "!", timeout=0.001)
