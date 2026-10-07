"""Read-only integration and transaction-boundary regression tests."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import apt
import apt_pkg

from orbit_gtk.backend.apt_cache import OrbitAptCache
from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.helper import _write_atomically, approve, configure_mirrors
from orbit_gtk.backend.history import _load_apt_history, _load_nala_history, _package_records
from orbit_gtk.backend.mirrors import (
    SourceSettings,
    normalize_mirror_url,
    parse_masterlist,
    render_orbit_source,
)
from orbit_gtk.backend.progress import DownloadProgress, InstallProgress
from orbit_gtk.backend.transactions import TransactionRejected, execute, mark_changes


def package(
    name="example",
    *,
    held=False,
    essential=False,
    protected=False,
    delete=False,
    downgrade=False,
    installed=True,
):
    return SimpleNamespace(
        name=name,
        fullname=f"{name}:amd64",
        essential=essential,
        _pkg=SimpleNamespace(
            selected_state=apt_pkg.SELSTATE_HOLD if held else apt_pkg.SELSTATE_INSTALL
        ),
        installed=SimpleNamespace(version="1.0", record={"Protected": "yes" if protected else "no"})
        if installed
        else None,
        candidate=SimpleNamespace(version="2.0", uris=["https://example.org/example.deb"]),
        is_installed=installed,
        marked_delete=delete,
        marked_downgrade=downgrade,
        mark_install=MagicMock(),
        mark_delete=MagicMock(),
    )


def cache_with(pkg):
    cache = MagicMock()
    cache.__contains__.return_value = True
    cache.__getitem__.return_value = pkg
    cache.get_changes.return_value = [pkg]
    cache.broken_count = 0
    cache.required_download = 1024
    cache.required_space = -512
    cache.__enter__.return_value = cache
    return cache


class TransactionTests(unittest.TestCase):
    def test_cleanup_does_not_hide_permission_errors(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError("Access denied")):
            with self.assertRaises(PermissionError):
                AptManager().get_cleanup_items()

    def test_rejects_cli_syntax_and_versions(self):
        for name in ("--yes", "bash;id", "bash=1", "foo/bar", "bash\n", "../etc"):
            with self.subTest(name=name), self.assertRaises(TransactionRejected):
                mark_changes(cache_with(package()), "install", [name])

    def test_held_requested_package(self):
        with self.assertRaisesRegex(TransactionRejected, "held"):
            mark_changes(cache_with(package(held=True)), "install", ["example"])

    def test_hold_and_uninstalled_size_snapshot(self):
        view = OrbitAptCache()
        pkg = package(held=True)
        pkg.is_upgradable = False
        pkg.is_auto_removable = False
        pkg.is_auto_installed = False
        pkg.installed.installed_size = 4096
        for field, value in {
            "summary": "Test",
            "description": "Test",
            "section": "misc",
            "homepage": "",
            "priority": "optional",
            "size": 1024,
            "installed_size": 8192,
        }.items():
            setattr(pkg.candidate, field, value)
        with patch.object(view, "_origin_name", return_value="Debian"):
            info = view._package_info(pkg)
            self.assertTrue(info.is_held)
            self.assertFalse(info.is_held_back)
            pkg.installed = None
            pkg.is_installed = False
            info = view._package_info(pkg)
            self.assertEqual(info.size_bytes, 8192)
            self.assertEqual(info.download_size_bytes, 1024)

    def test_autoremove_only_marks_unused_installed_dependencies(self):
        unused = package(delete=True)
        unused.is_auto_removable = True
        manual = package(name="manual")
        manual.is_auto_removable = False
        cache = cache_with(unused)
        cache.__iter__.return_value = iter([unused, manual])
        result = mark_changes(cache, "autoremove", [])
        unused.mark_delete.assert_called_once_with(auto_fix=False, purge=False)
        manual.mark_delete.assert_not_called()
        self.assertEqual(result[0]["action"], "remove")
        with self.assertRaises(TransactionRejected):
            mark_changes(cache, "autoremove", ["example"])

    def test_autoremove_rejects_protected_held_and_unexpected_changes(self):
        for kwargs in ({"essential": True}, {"held": True}, {"protected": True}):
            pkg = package(delete=True, **kwargs)
            pkg.is_auto_removable = True
            cache = cache_with(pkg)
            cache.__iter__.return_value = iter([pkg])
            with self.assertRaises(TransactionRejected):
                mark_changes(cache, "autoremove", [])
        pkg = package(delete=True)
        pkg.is_auto_removable = False
        cache = cache_with(pkg)
        cache.__iter__.return_value = iter([pkg])
        with self.assertRaisesRegex(TransactionRejected, "outside"):
            mark_changes(cache, "autoremove", [])

    def test_reinstall_pins_installed_version_and_rejects_missing_archive(self):
        pkg = package()
        installed_archive = SimpleNamespace(version="1.0", downloadable=True)
        pkg.versions = {"1.0": installed_archive}
        cache = cache_with(pkg)
        # Same-version reinstalls can remain in APT's Keep mode and therefore
        # disappear from python-apt's ordinary change list.
        cache.get_changes.return_value = []
        result = mark_changes(cache, "reinstall", ["example"])
        self.assertIs(pkg.candidate, installed_archive)
        self.assertEqual(result[0]["action"], "reinstall")
        self.assertEqual(result[0]["new_version"], "1.0")
        cache._depcache.set_reinstall.assert_called_once_with(pkg._pkg, True)
        installed_archive.downloadable = False
        with self.assertRaisesRegex(TransactionRejected, "no longer downloadable"):
            mark_changes(cache, "reinstall", ["example"])
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(package(held=True)), "reinstall", ["example"])
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(package(installed=False)), "reinstall", ["example"])

    def test_purge_plan_and_protected_guard(self):
        pkg = package(delete=True)
        changes = mark_changes(cache_with(pkg), "purge", ["example"])
        pkg.mark_delete.assert_called_once_with(auto_fix=True, purge=True)
        self.assertEqual(changes[0]["action"], "purge")
        self.assertIsNone(changes[0]["new_version"])
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(package(delete=True, essential=True)), "purge", ["example"])
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(package(held=True)), "purge", ["example"])

    def test_protected_dependency_removal(self):
        for kwargs in ({"essential": True}, {"protected": True}):
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaisesRegex(TransactionRejected, "protected"),
            ):
                mark_changes(cache_with(package(delete=True, **kwargs)), "remove", ["example"])

    def test_upgrade_cannot_remove(self):
        with self.assertRaisesRegex(TransactionRejected, "must not remove"):
            mark_changes(cache_with(package(delete=True)), "upgrade", [])

    def test_full_upgrade_reviews_removals_and_preserves_guards(self):
        cache = cache_with(package(delete=True))
        changes = mark_changes(cache, "full-upgrade", [])
        cache.upgrade.assert_called_once_with(dist_upgrade=True)
        self.assertEqual(changes[0]["action"], "remove")
        for flags in ({"held": True}, {"essential": True}, {"protected": True}):
            with self.subTest(flags=flags), self.assertRaises(TransactionRejected):
                mark_changes(cache_with(package(delete=True, **flags)), "full-upgrade", [])
        with self.assertRaises(TransactionRejected):
            mark_changes(cache_with(package(downgrade=True)), "full-upgrade", [])

    def test_health_distinguishes_configuration_from_reinstallation(self):
        cache = MagicMock()
        cache.broken_count = 2
        cache.__iter__.return_value = iter(
            [
                SimpleNamespace(
                    fullname=name, _pkg=SimpleNamespace(current_state=state, inst_state=flag)
                )
                for name, state, flag in [
                    ("installed", 6, 0),
                    ("absent", 0, 0),
                    ("config-only", 5, 0),
                    ("unpacked", 1, 0),
                    ("half-configured", 2, 0),
                    ("triggers", 7, 0),
                    ("half-installed", 4, 0),
                    ("reinstall", 6, 1),
                    ("held-reinstall", 6, 3),
                ]
            ]
        )
        view = OrbitAptCache()
        with patch.object(view, "_require_cache", return_value=cache):
            health = view.get_health()
        self.assertEqual(health.pending_configuration, ("half-configured", "triggers", "unpacked"))
        self.assertEqual(
            health.reinstall_required, ("half-installed", "held-reinstall", "reinstall")
        )
        self.assertEqual(health.broken_dependencies, 2)
        self.assertTrue(health.needs_attention)
        cache.commit.assert_not_called()

    def test_downgrade_is_rejected(self):
        with self.assertRaisesRegex(TransactionRejected, "downgrade"):
            mark_changes(cache_with(package(downgrade=True)), "install", ["example"])

    def test_broken_dependencies_rejected(self):
        cache = cache_with(package())
        cache.broken_count = 1
        with self.assertRaisesRegex(TransactionRejected, "Dependencies"):
            mark_changes(cache, "install", ["example"])

    def test_dependency_plan_includes_versions_and_architecture(self):
        result = mark_changes(cache_with(package()), "install", ["example"])
        self.assertEqual(
            result,
            [
                {
                    "name": "example:amd64",
                    "action": "upgrade",
                    "old_version": "1.0",
                    "new_version": "2.0",
                }
            ],
        )

    def test_cancel_never_commits(self):
        cache = cache_with(package())
        emit = MagicMock()
        with (
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
        ):
            self.assertFalse(execute("install", ["example"], emit, lambda _: False))
        cache.commit.assert_not_called()

    def test_commit_requires_review_and_authentication(self):
        cache = cache_with(package())
        reviewed = []
        with (
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
        ):
            result = execute(
                "install", ["example"], MagicMock(), lambda plan: reviewed.append(plan) or True
            )
        self.assertTrue(result)
        self.assertEqual(reviewed[0]["download_bytes"], 1024)
        self.assertFalse(cache.commit.call_args.kwargs["allow_unauthenticated"])

    def test_false_commit_is_failure(self):
        cache = cache_with(package())
        cache.commit.return_value = False
        with (
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
            self.assertRaisesRegex(RuntimeError, "did not complete"),
        ):
            execute("install", ["example"], MagicMock(), lambda _: True)

    def test_no_changes_skips_review_and_commit(self):
        cache = cache_with(package())
        cache.get_changes.return_value = []
        review = MagicMock()
        with (
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
        ):
            self.assertTrue(execute("upgrade", [], MagicMock(), review))
        review.assert_not_called()
        cache.commit.assert_not_called()

    def test_all_kept_back_still_emits_structured_event(self):
        pkg = package()
        pkg.is_upgradable = True
        cache = cache_with(pkg)
        cache.__iter__.return_value = iter([pkg])
        cache.get_changes.return_value = []
        emit = MagicMock()
        with (
            patch("orbit_gtk.backend.transactions.apt_pkg.SystemLock"),
            patch("orbit_gtk.backend.transactions.apt.Cache", return_value=cache),
        ):
            self.assertTrue(execute("upgrade", [], emit, MagicMock()))
        emit.assert_any_call("kept-back", packages=["example:amd64"])
        cache.commit.assert_not_called()

    def test_readonly_upgrade_policy_marks_excluded_candidates(self):
        from orbit_gtk.backend.models import PackageInfo

        selected = SimpleNamespace(fullname="selected:amd64", is_upgradable=True)
        excluded = SimpleNamespace(fullname="excluded:i386", is_upgradable=True)
        cache = MagicMock()
        cache.__iter__.return_value = iter([selected, excluded])
        cache.get_changes.return_value = [selected]
        view = OrbitAptCache()
        view._cache = cache
        view._initialized = True
        with patch.object(
            view, "_package_info", side_effect=lambda p: PackageInfo(name=p.fullname)
        ):
            packages = view.get_upgradable()
        self.assertTrue(packages[0].is_held_back)
        self.assertFalse(packages[1].is_held_back)
        cache.upgrade.assert_called_once_with(dist_upgrade=False)
        cache.clear.assert_called_once()
        self.assertEqual(view.get_upgradable(), packages)
        cache.upgrade.assert_called_once()

    def test_upgrade_simulation_failure_is_visible_and_clears_marks(self):
        from orbit_gtk.backend.apt_cache import AptCacheUnavailable

        cache = MagicMock()
        cache.upgrade.side_effect = RuntimeError("Broken dependencies")
        view = OrbitAptCache()
        view._cache = cache
        view._initialized = True
        with self.assertRaisesRegex(AptCacheUnavailable, "Broken dependencies"):
            view.get_upgradable()
        cache.clear.assert_called_once()
        self.assertNotIn("upgradable", view._snapshots)

    def test_abandoned_review_fails_closed(self):
        with patch("orbit_gtk.backend.helper.select.select", return_value=([], [], [])):
            self.assertFalse(approve({}, MagicMock()))


class ProgressTests(unittest.TestCase):
    def test_download_percent_handles_unknown_total(self):
        events = []
        progress = DownloadProgress(lambda kind, **p: events.append(p))
        self.assertTrue(progress.pulse(None))
        self.assertIsNone(events[-1]["percent"])
        progress.current_bytes = 25
        progress.total_bytes = 100
        progress.pulse(None)
        self.assertEqual(events[-1]["percent"], 25)

    def test_each_download_uses_its_own_byte_count(self):
        events = []
        progress = DownloadProgress(
            lambda kind, **p: events.append((kind, p)),
            {"https://example.org/a.deb": "a:amd64", "https://example.org/b.deb": "b:amd64"},
        )
        workers = [
            SimpleNamespace(
                current_item=SimpleNamespace(uri=uri), current_size=size, total_size=100
            )
            for uri, size in (("https://example.org/a.deb", 25), ("https://example.org/b.deb", 75))
        ]
        progress.pulse(SimpleNamespace(workers=workers))
        packages = {
            payload["package"]: payload["percent"]
            for kind, payload in events
            if kind == "package-progress"
        }
        self.assertEqual(packages, {"a:amd64": 25, "b:amd64": 75})

    def test_install_names_match_plan_without_guessing_architecture(self):
        events = []
        with InstallProgress(
            lambda kind, **p: events.append(p), ["bash:amd64", "lib:i386", "lib:amd64"]
        ) as progress:
            progress.status_change("bash", 10, "Unpacking bash")
            progress.status_change("lib", 20, "Configuring lib")
            progress.status_change("lib:i386", 30, "Installed lib")
            progress.status_change("lib", 40, "Unpacking lib (amd64)")
        self.assertEqual(
            [event["package"] for event in events], ["bash:amd64", "lib", "lib:i386", "lib:amd64"]
        )

    def test_install_callback_preserves_package_and_stage(self):
        events = []
        with InstallProgress(lambda kind, **p: events.append(p)) as progress:
            progress.status_change("bash:amd64", 32.5, "Unpacking bash")
        self.assertEqual(events[-1]["percent"], 32.5)
        self.assertEqual(events[-1]["message"], "Unpacking bash")
        self.assertEqual(events[-1]["package"], "bash:amd64")


class MirrorTests(unittest.TestCase):
    def test_reject_source_injection_and_invalid_urls(self):
        for url in (
            "https://example.org/debian\nTrusted: yes",
            "https://example.org/a b",
            "https://example.org\t/debian",
            "file:///etc",
            "https://user:pass@example.org/debian",
            "https://example.org:abc/debian",
            "https://example.org/debian?q=a",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_mirror_url(url)

    def test_architecture_match_is_exact(self):
        raw = "Site: example.org\nArchive-http: /debian/\nArchive-architecture: arm64 amd64\nCountry: IN India\n"
        self.assertEqual(len(parse_masterlist(raw, ["arm64", "amd64"])), 1)
        self.assertEqual(parse_masterlist(raw, ["arm"]), [])
        self.assertEqual(
            parse_masterlist(raw.replace("Country: IN India", "Country:"), ["amd64"])[
                0
            ].country_code,
            "",
        )

    def test_source_injection_rejected(self):
        with self.assertRaises(ValueError):
            render_orbit_source(
                ["https://example.org/debian"], SourceSettings("sid", ("main\nTrusted: yes",))
            )

    def test_atomic_write_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sources"
            _write_atomically(path, "old")
            _write_atomically(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(path.with_suffix(".sources.bak").read_text(), "old")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_save_mirrors_skips_duplicates_without_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sources"
            with (
                patch("orbit_gtk.backend.helper.ORBIT_SOURCES_PATH", path),
                patch(
                    "orbit_gtk.backend.helper.source_settings",
                    return_value=SourceSettings("sid", ("main",)),
                ),
                patch(
                    "orbit_gtk.backend.helper.existing_mirror_urls",
                    return_value={"https://existing.org/debian"},
                ),
                patch("orbit_gtk.backend.transactions.update") as update,
                patch("orbit_gtk.backend.helper.validate_mirror"),
            ):
                configure_mirrors(
                    ["https://existing.org/debian", "https://new.org/debian"], "sid", MagicMock()
                )
                self.assertIn("https://new.org/debian", path.read_text())
                self.assertNotIn("https://existing.org/debian", path.read_text())
                configure_mirrors(None, None, MagicMock())
                self.assertFalse(path.exists())
                update.assert_not_called()

    def test_existing_sources_support_both_formats_and_ignore_disabled(self):
        from orbit_gtk.backend.mirrors import existing_mirror_urls

        with tempfile.TemporaryDirectory() as directory:
            traditional = Path(directory) / "sources.list"
            traditional.write_text(
                "# deb https://ignored.org/debian sid main\n"
                "deb [signed-by=/key.gpg] https://one.org/debian/ sid main\n"
                "deb https://wrong-suite.org/debian stable main\n"
            )
            deb822 = Path(directory) / "debian.sources"
            deb822.write_text(
                "Types: deb deb-src\nURIs: https://two.org/debian\nSuites: sid\n\n"
                "Types: deb\nURIs: https://disabled.org/debian\nSuites: sid\nEnabled: no\n"
            )
            self.assertEqual(
                existing_mirror_urls("sid", [traditional, deb822]),
                {"https://one.org/debian", "https://two.org/debian"},
            )


class HistoryTests(unittest.TestCase):
    def test_apt_versions_and_purge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.log"
            path.write_text(
                "Start-Date: 2026-01-01  10:00:00\nUpgrade: bash:amd64 (1, 2)\nPurge: old:amd64 (1)\nEnd-Date: 2026-01-01  10:00:02\n"
            )
            entry = _load_apt_history((path,))[0]
            self.assertEqual(entry.upgraded_pkgs[0].latest_version, "2")
            self.assertEqual(entry.purged_pkgs[0].name, "old:amd64")
            self.assertEqual(entry.removed_pkgs, [])

    def test_mixed_apt_history_preserves_each_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.log"
            path.write_text(
                "Start-Date: 2026-01-01  10:00:00\n"
                "Install: added:amd64 (1, automatic)\n"
                "Remove: removed:amd64 (2)\nPurge: purged:amd64 (3)\n"
                "Reinstall: same:amd64 (4)\nDowngrade: older:amd64 (5, 4)\n"
                "End-Date: 2026-01-01  10:01:00\n"
            )
            entry = _load_apt_history((path,))[0]
            self.assertEqual(entry.altered_count, 5)
            self.assertEqual(entry.removed_pkgs[0].name, "removed:amd64")
            self.assertEqual(entry.purged_pkgs[0].name, "purged:amd64")
            self.assertEqual(entry.reinstalled_pkgs[0].latest_version, "4")
            self.assertEqual(entry.downgraded_pkgs[0].installed_version, "5")
            self.assertEqual(entry.downgraded_pkgs[0].latest_version, "4")

    def test_nala_purge_and_additional_actions(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text(
                json.dumps(
                    {
                        "1": {
                            "Operation": "remove",
                            "Purged": True,
                            "Removed": [["old", "1", "10"]],
                            "Auto-Removed": [["dep", "2", "20"]],
                            "Reinstalled": [["same", "1", "10"]],
                            "Downgraded": [["older", "1", "10", "2"]],
                        }
                    }
                )
            )
            entry = _load_nala_history(path)[0]
            self.assertEqual(entry.operation, "purge")
            self.assertEqual(entry.removed_pkgs, [])
            self.assertEqual(len(entry.purged_pkgs), 2)
            self.assertEqual(entry.altered_count, 4)
            self.assertEqual(entry.downgraded_pkgs[0].installed_version, "2")

    def test_nala_legacy_upgrade_layouts(self):
        for record in (["pkg", "2.0", "1024", "1.0"], ["pkg", "1.0", "2.0", "1024"]):
            parsed = _package_records([record], "upgrade")[0]
            self.assertEqual(
                (parsed.installed_version, parsed.latest_version, parsed.size_bytes),
                ("1.0", "2.0", 1024),
            )

    def test_malformed_nala_history_is_not_fake_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text("[]")
            self.assertEqual(_load_nala_history(path), [])


class RealCacheTests(unittest.TestCase):
    def test_read_only_cache_search_and_snapshots(self):
        cache = OrbitAptCache()
        self.assertFalse(cache._initialized)
        installed = cache.get_installed()
        self.assertTrue(installed)
        self.assertEqual(installed, cache.get_installed())
        matches = cache.search("bash", limit=5)
        self.assertLessEqual(len(matches), 5)
        self.assertEqual(matches[0].name, "bash")
        self.assertEqual(cache.search(""), [])
        self.assertEqual(cache.search("bash", cancelled=lambda: True), [])
        # Descriptions must remain searchable when index traversal order changes.
        self.assertIn("bash", [item.name for item in cache.search("bourne again")])

    def test_real_resolver_plan_never_removes_essential_package(self):
        with apt.Cache() as cache:
            with self.assertRaises(TransactionRejected):
                mark_changes(cache, "remove", ["bash"])

    def test_errors_are_thread_local(self):
        manager = AptManager()
        manager._errors.message = "main"

        def worker():
            manager._errors.message = "worker"

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(manager.last_error, "main")


if __name__ == "__main__":
    unittest.main()
