"""GTK integration checks. Run under xvfb-run; never authorizes system changes."""

import os
import subprocess
import sys
import time
import unittest

# Xvfb must not connect its short-lived surfaces to the desktop IBus daemon.
# Override explicitly for dedicated input-method integration testing.
if os.environ.get("ORBIT_GUI_TESTS") == "1":
    os.environ["GTK_IM_MODULE"] = os.environ.get("ORBIT_TEST_IM_MODULE", "simple")

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.ui.operation_dialog import OperationDialog
from orbit_gtk.ui.window import OrbitWindow


@unittest.skipUnless(
    os.environ.get("ORBIT_GUI_TESTS") == "1", "Run with ORBIT_GUI_TESTS=1 under Xvfb"
)
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        cls.app = Adw.Application(
            application_id="io.github.orbit.Test", flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        cls.app.register(None)
        cls.errors = []
        cls.old_hook = sys.excepthook
        sys.excepthook = lambda *error: cls.errors.append(error)

    @classmethod
    def tearDownClass(cls):
        sys.excepthook = cls.old_hook
        if cls.errors:
            raise AssertionError(cls.errors)

    def setUp(self):
        self.window = OrbitWindow(application=self.app, apt_manager=AptManager())
        self.window.present()

    def tearDown(self):
        self.window._operation_active = False
        self.window.close()
        self.spin(lambda: True)

    @staticmethod
    def spin(condition, timeout=30):
        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            for _ in range(30):
                if not context.pending():
                    break
                context.iteration(False)
            if condition():
                return
            time.sleep(0.01)
        raise AssertionError("Timed out waiting for GTK state")

    def test_mirror_sorting_and_live_source_badges(self):
        from orbit_gtk.backend.models import MirrorInfo

        page = self.window._pages["mirrors"]
        urls = [
            "https://slow.example/debian",
            "https://fast.example/debian",
            "https://middle.example/debian",
        ]
        for url, latency in zip(urls, [300, 100, 200], strict=True):
            page._on_result(MirrorInfo(domain=url, url=url, country_code="", latency_ms=latency))
        page._show_active([urls[0]], {urls[1]})
        rows = [page._results.get_row_at_index(i) for i in range(3)]
        self.assertEqual([row._url for row in rows], [urls[1], urls[2], urls[0]])
        self.assertEqual([row._use.get_label() for row in rows], ["In use", "Use", "In use"])
        self.assertFalse(rows[0]._use.get_sensitive())
        from unittest.mock import patch

        with patch.object(page, "_apply_mirrors") as apply:
            page._use_best(2)
        apply.assert_called_once_with([urls[1], urls[2]])
        page._show_active([], {urls[1]})
        self.assertEqual(rows[2]._use.get_label(), "Use")
        self.assertTrue(rows[2]._use.get_sensitive())

    def test_kept_back_badge_visible_without_progress_or_plan(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["updates"]
        page._apply(
            page._generation, [PackageInfo(name="example", full_name="example:amd64")], None
        )
        page._operation_event({"event": "kept-back", "packages": ["example:amd64", "example:i386"]})
        for key in ("example:amd64", "example:i386"):
            widgets = page._row_widgets[key]
            self.assertTrue(widgets["badge"].get_visible())
            self.assertTrue(widgets["badge"].has_css_class("warning"))
            self.assertFalse(widgets["progress_box"].get_visible())
        from types import SimpleNamespace
        from unittest.mock import patch

        page._operation = SimpleNamespace(_cancelled=False, dismiss_automatically=False)
        with patch.object(self.window, "refresh_all"):
            page._operation_done(True)
        self.assertTrue(page._row_widgets["example:amd64"]["badge"].get_visible())

        page._operation_event(
            {
                "event": "plan",
                "changes": [
                    {
                        "name": "example:amd64",
                        "action": "upgrade",
                        "old_version": "1",
                        "new_version": "2",
                    }
                ],
                "kept_back": [],
            }
        )
        self.assertFalse(page._row_widgets["example:amd64"]["badge"].get_visible())
        page._pending.clear()
        self.assertFalse(page._row_widgets["example:amd64"]["progress_box"].get_visible())
        page._operation = None
        page._apply(
            page._generation,
            [
                PackageInfo(
                    name="example",
                    full_name="example:amd64",
                    is_held_back=True,
                    held_reason="APT policy",
                )
            ],
            None,
        )
        self.assertTrue(page._row_widgets["example:amd64"]["badge"].get_visible())

    def test_application_icons_and_fallback(self):
        import tempfile
        from pathlib import Path

        from orbit_gtk.backend.models import PackageInfo
        from orbit_gtk.ui.widgets import _load_texture, package_icon

        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "icon.png"
            from gi.repository import Gdk

            texture = Gdk.MemoryTexture.new(
                1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([255, 128, 0, 255])), 4
            )
            texture.save_to_png(str(filename))
            widget = package_icon(PackageInfo(name="example", icon_file=str(filename)))
            self.spin(lambda: widget.get_paintable() is not None)
            self.assertEqual(widget.get_pixel_size(), 32)
            self.assertEqual(widget.get_paintable().get_intrinsic_width(), 1)
            fallback = package_icon(PackageInfo(name="library", icon_name="no-such-orbit-icon"))
            self.assertEqual(fallback.get_icon_name(), "package-x-generic-symbolic")
            self.assertEqual(fallback.get_pixel_size(), 32)
            filename.write_text("corrupt image")
            self.assertIsNone(_load_texture(str(filename), filename.stat().st_mtime_ns))

    def test_real_appstream_artwork(self):
        from orbit_gtk.backend.appstream import PackageMetadata
        from orbit_gtk.backend.models import PackageInfo
        from orbit_gtk.ui.widgets import package_icon

        package = PackageMetadata().decorate([PackageInfo(name="gimp")])[0]
        if not package.icon_file:
            self.skipTest("GIMP AppStream artwork is not cached on this system")
        widget = package_icon(package, 64)
        self.spin(lambda: widget.get_paintable() is not None)
        self.assertGreaterEqual(widget.get_paintable().get_intrinsic_width(), 48)
        if os.environ.get("ORBIT_ICONS_SCREENSHOT"):
            browse = self.window._pages["browse"]
            self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(2))
            browse._entry.set_text("gimp")
            self.spin(lambda: browse._stack.get_visible_child_name() == "results")
            start = time.monotonic()
            self.spin(lambda: time.monotonic() - start > 1)
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_ICONS_SCREENSHOT"]], check=True
            )

    def test_screenshot_gallery_navigation_failure_and_stale_load(self):
        from concurrent.futures import Future
        from unittest.mock import patch

        from gi.repository import Gdk

        from orbit_gtk.backend.models import ScreenshotInfo
        from orbit_gtk.ui.screenshots import ScreenshotGallery

        texture = Gdk.MemoryTexture.new(
            1, 1, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([255, 128, 0, 255])), 4
        )
        gallery = ScreenshotGallery(
            (
                ScreenshotInfo("https://example.org/one", "First", 752, 423),
                ScreenshotInfo("https://example.org/two", "Second", 752, 423),
            )
        )
        first, second, retry = Future(), Future(), Future()
        first.set_running_or_notify_cancel()
        with patch("orbit_gtk.ui.screenshots._WORKERS.submit", side_effect=[first, second, retry]):
            self.assertEqual(gallery._stack.get_visible_child_name(), "loading")
            gallery._map(gallery)
            gallery._map(gallery)  # Mapping again must not start another request.
            gallery._move(1)
            second.set_exception(OSError("offline"))
            self.spin(lambda: gallery._stack.get_visible_child_name() == "error")
            first.set_result(texture)
            self.spin(lambda: True)
            self.assertEqual(gallery._stack.get_visible_child_name(), "error")
            self.assertEqual(gallery._caption.get_label(), "Second")
            self.assertFalse(gallery._next.get_sensitive())
            gallery._show()
            retry.set_result(texture)
            self.spin(lambda: gallery._stack.get_visible_child_name() == "image")
            self.assertEqual(gallery._counter.get_label(), "2 / 2")
            self.assertFalse(gallery._spinner.get_spinning())
            gallery._unmap(gallery)

    def test_history_chips_groups_and_large_package_list(self):
        from orbit_gtk.backend.models import HistoryTransaction, PackageInfo

        page = self.window._pages["history"]
        transaction = HistoryTransaction(
            "test",
            "2026-09-23",
            "test user",
            "apt upgrade",
            "upgrade",
            502,
            upgraded_pkgs=[
                PackageInfo(name=f"package-{i}", installed_version="1", latest_version="2")
                for i in range(500)
            ],
            removed_pkgs=[PackageInfo(name="removed", installed_version="1")],
            purged_pkgs=[PackageInfo(name="purged", installed_version="1")],
        )
        row = page._make_row(transaction)
        page._group.add(row)

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        self.assertFalse(any(isinstance(w, Gtk.ListView) for w in descendants(row)))
        row.set_expanded(True)
        groups = {w.get_title(): w for w in descendants(row) if isinstance(w, Adw.ExpanderRow)}
        self.assertIn("Purged", groups)
        self.assertIn("Removed", groups)
        labels = [w.get_label() for w in descendants(row) if isinstance(w, Gtk.Label)]
        self.assertIn("500 upgraded", labels)
        self.assertIn("1 purged", labels)
        views = [w for w in descendants(row) if isinstance(w, Gtk.ListView)]
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].get_model().get_n_items(), 500)
        groups["Purged"].set_expanded(True)
        views = [w for w in descendants(row) if isinstance(w, Gtk.ListView)]
        self.assertEqual(len(views), 2)
        row.set_expanded(False)
        row.set_expanded(True)
        self.assertEqual(len([w for w in descendants(row) if isinstance(w, Gtk.ListView)]), 2)
        self.assertEqual(page._package_subtitle("upgrade", transaction.upgraded_pkgs[0]), "1 → 2")

    def test_installed_filter_cannot_hide_loading_or_error(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["installed"]
        page._packages = [PackageInfo(name="stale")]
        page._data_ready = False
        page._stack.set_visible_child_name("loading")
        page._apply_filter()
        self.assertEqual(page._stack.get_visible_child_name(), "loading")
        page._apply(page._generation, [], "Unreadable APT cache")
        page._apply_filter_debounced()
        self.assertEqual(page._stack.get_visible_child_name(), "error")
        self.assertEqual(page._summary.get_label(), "Package data unavailable")

    def test_installed_large_catalogue_recycles_rows_and_actions_follow_filter(self):
        from unittest.mock import patch

        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["installed"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(3))
        page._generation += 1  # Discard the real asynchronous snapshot for this fixture.
        packages = [
            PackageInfo(
                name=f"package-{i:05}",
                full_name=f"package-{i:05}:amd64",
                installed_version="1",
                is_installed=True,
                is_held=i == 9999,
            )
            for i in range(10000)
        ]
        page._apply(page._generation, packages, None)
        self.spin(lambda: page._adjustment.get_upper() > page._adjustment.get_page_size())

        def row_count():
            count = 0
            child = page._list.get_first_child()
            while child:
                count += 1
                child = child.get_next_sibling()
            return count

        self.assertLess(row_count(), 1000)
        page._adjustment.set_value(page._adjustment.get_upper() - page._adjustment.get_page_size())
        self.spin(lambda: page._adjustment.get_value() > 0)
        self.assertLess(row_count(), 1000)
        self.assertEqual(page._model.get_n_items(), 10000)
        page._apply(page._generation, packages, None)
        settled = []
        GLib.timeout_add(150, lambda: settled.append(True) and False)
        self.spin(lambda: bool(settled))
        self.assertLessEqual(page._adjustment.get_value(), 12)
        page._entry.set_text("package-09999")
        self.spin(lambda: page._model.get_n_items() == 1)
        with patch.object(self.window, "show_package_details") as show:
            page._list.emit("activate", 0)
            self.assertIs(show.call_args.args[0], packages[-1])
        held_row = page._make_row(packages[-1])
        self.assertFalse(held_row.get_last_child().get_sensitive())
        page._entry.set_text("package-00042")
        self.spin(lambda: page._model.get_item(0).package.name == "package-00042")
        row = page._make_row(page._model.get_item(0).package)
        with patch.object(self.window, "confirm_and_run") as remove:
            row.get_last_child().emit("clicked")
            self.assertEqual(remove.call_args.args[2][-1], "package-00042:amd64")
        page._entry.set_text("not-in-catalogue")
        self.spin(lambda: page._stack.get_visible_child_name() == "empty")
        self.assertEqual(page._model.get_n_items(), 0)
        with patch.object(self.window, "show_package_details") as show:
            page._activate(page._list, 0)
            show.assert_not_called()

    def test_transaction_rows_keep_install_stages_after_filtering(self):
        from orbit_gtk.ui.transaction_plan import TransactionPlan

        plan = TransactionPlan()
        plan.set_changes(
            [
                {
                    "name": "example:amd64",
                    "action": "install",
                    "old_version": None,
                    "new_version": "1",
                }
            ]
        )
        plan.update_progress(
            {
                "event": "package-progress",
                "package": "example:amd64",
                "message": "Downloading",
                "percent": 25,
            }
        )
        self.assertEqual(plan._progress_state["example:amd64"], ("Downloading", 25))
        plan.update_progress(
            {"event": "progress", "package": "example:amd64", "message": "Unpacking", "percent": 90}
        )
        self.assertEqual(plan._progress_state["example:amd64"], ("Unpacking", None))
        plan._search.set_text("missing")
        plan._filter()
        plan._search.set_text("")
        plan._filter()
        self.assertEqual(plan._progress_state["example:amd64"], ("Unpacking", None))
        plan.finish(True)
        self.assertEqual(plan._progress_state["example:amd64"], ("Completed", 100))

    def test_visible_local_install_control_opens_filtered_chooser(self):
        from unittest.mock import MagicMock, patch

        from gi.repository import Gio

        page = self.window._pages["browse"]
        self.window.navigate("browse")
        self.assertTrue(page._local_button.get_visible())
        self.assertEqual(page._local_button.get_label(), "Browse")
        if os.environ.get("ORBIT_LOCAL_SCREENSHOT"):
            self.window.set_default_size(800, 640)
            start = time.monotonic()
            self.spin(lambda: time.monotonic() - start > 0.5)
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_LOCAL_SCREENSHOT"]], check=True
            )
        chooser = MagicMock()
        with (
            patch("orbit_gtk.ui.window.Gtk.FileDialog", return_value=chooser),
            patch.object(self.window, "install_local") as install,
        ):
            page._local_button.emit("clicked")
            chooser.open.assert_called_once()
            filters = chooser.set_filters.call_args.args[0]
            self.assertEqual(filters.get_item(0).get_name(), "Debian packages (.deb)")
            chooser.open_finish.return_value = Gio.File.new_for_path("/tmp/downloaded package.deb")
            callback = chooser.open.call_args.args[2]
            callback(chooser, None)
            install.assert_called_once_with("/tmp/downloaded package.deb")
        self.window._operation_active = True
        with patch("orbit_gtk.ui.window.Gtk.FileDialog") as create:
            page._local_button.emit("clicked")
            create.assert_not_called()
        self.window._operation_active = False

    def test_command_routes_to_pages_and_review(self):
        from unittest.mock import patch

        from orbit_gtk.cli import parse_command

        for command, page in (
            ("history", "history"),
            ("list --installed", "installed"),
            ("list --upgradable", "updates"),
            ("search editor", "browse"),
        ):
            self.window.dispatch_command(parse_command(command.split()))
            self.assertEqual(self.window._stack.get_visible_child_name(), page)
        with patch.object(self.window, "run_privileged") as run:
            self.window.dispatch_command(parse_command(["install", "bash"]))
            self.assertEqual(run.call_args.args[1][-2:], ["install", "bash"])
            self.window.dispatch_command(parse_command(["install", "./local.deb"], "/tmp/caller"))
            self.assertEqual(run.call_args.args[1][-2:], ["install-local", "/tmp/caller/local.deb"])
            run.reset_mock()
            self.window._operation_active = True
            self.window.dispatch_command(parse_command(["remove", "bash"]))
            run.assert_not_called()
            self.window._operation_active = False

    def test_derivative_mirror_page_explains_unsupported_catalogue(self):
        from unittest.mock import patch

        from orbit_gtk.backend.mirrors import UnsupportedMirrorDistribution

        page = self.window._pages["mirrors"]
        with patch(
            "orbit_gtk.ui.pages.mirrors.source_settings",
            side_effect=UnsupportedMirrorDistribution("Debian only"),
        ):
            page._on_benchmark(None)
        self.assertTrue(page._unsupported.get_visible())
        self.assertFalse(page._content_scroll.get_visible())
        self.assertFalse(page._progress.get_visible())
        self.assertIsNone(page._worker)

    def test_full_upgrade_uses_inline_review(self):
        from unittest.mock import patch

        from orbit_gtk.ui.pages.updates import UpdatesPage

        page = UpdatesPage(self.window.apt_manager, self.window)
        with patch.object(page, "start_operation") as start:
            page._on_full_upgrade(None)
        self.assertEqual(start.call_args.args[0], "Review full upgrade")
        self.assertEqual(start.call_args.args[1][-1], "full-upgrade")

    def test_home_preserves_independent_failures_and_recovers(self):
        from types import SimpleNamespace

        from orbit_gtk.backend.models import PackageHealth, PackageInfo
        from orbit_gtk.ui.pages.home import HomePage

        manager = SimpleNamespace(
            last_error=None,
            format_size=AptManager.format_size,
            get_system_info=lambda: {"distro": "Test system"},
            get_package_health=lambda: PackageHealth(pending_configuration=("unfinished:amd64",)),
        )

        def installed():
            manager.last_error = "Installed snapshot unavailable"
            return []

        def updates():
            manager.last_error = None
            return []

        def cleanup():
            raise PermissionError("Cache unreadable")

        manager.get_installed_packages = installed
        manager.get_upgradable_packages = updates
        manager.get_cleanup_items = cleanup
        page = HomePage(manager, self.window)
        page.load_data()
        self.spin(lambda: page._retry.get_visible())
        self.assertIn("Installed snapshot unavailable", page._row_installed.get_subtitle())
        self.assertEqual(page._row_upgradable.get_subtitle(), "No cached upgrades")
        self.assertIn("Cache unreadable", page._row_cache.get_subtitle())
        self.assertEqual(page._row_distro.get_subtitle(), "Test system")
        self.assertTrue(page._recovery_group.get_visible())
        self.assertTrue(page._repair_button.get_visible())
        self.assertIn("unfinished:amd64", page._recovery.get_subtitle())
        manager.get_package_health = lambda: PackageHealth()
        manager.get_installed_packages = lambda: [PackageInfo(name="recovered")]
        manager.get_cleanup_items = lambda: []
        page.invalidate()
        self.spin(lambda: page._row_cache.get_subtitle() == "0 B")
        self.assertFalse(page._retry.get_visible())
        self.assertFalse(page._row_installed.has_css_class("error"))
        self.assertFalse(page._recovery_group.get_visible())

    def test_search_hides_stale_actions_and_resets_empty_state(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["browse"]
        page._entry.set_text("first")
        page._show_results("first", page._generation, [PackageInfo(name="first")], None)
        self.assertEqual(page._stack.get_visible_child_name(), "results")
        page._entry.set_text("second")
        self.assertEqual(page._stack.get_visible_child_name(), "loading")
        self.assertIsNone(page._list.get_first_child())
        page._show_results("second", page._generation, [], None)
        self.assertEqual(page._placeholder.get_title(), "No packages found")
        page._entry.set_text("")
        self.assertEqual(page._placeholder.get_title(), "Search packages")
        self.assertEqual(page._stack.get_visible_child_name(), "placeholder")

    def test_offline_refresh_failure_keeps_cached_browsing_available(self):
        page = self.window._pages["updates"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(1))
        script = 'import json; print(json.dumps(dict(event="error", message="Repository refresh failed. Check your connection; cached data remains available.")), flush=True); raise SystemExit(1)'
        page.start_operation("Refresh package lists", [sys.executable, "-c", script])
        self.spin(lambda: page._operation._finished)
        self.assertEqual(page._operation._phase.get_label(), "Operation failed")
        self.assertEqual(page._operation._progress.get_fraction(), 0)
        page._dismiss_operation()
        self.assertFalse(self.window._operation_active)
        installed = self.window._pages["installed"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(3))
        self.spin(lambda: installed._data_ready)
        self.assertGreater(installed._model.get_n_items(), 0)
        browse = self.window._pages["browse"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(2))
        browse._entry.set_text("bash")
        self.spin(lambda: browse._stack.get_visible_child_name() == "results")
        self.assertEqual(browse._list.get_first_child().get_title(), "bash")
        mirrors = self.window._pages["mirrors"]
        mirrors._on_done([], None)
        self.assertIn("Check your connection", mirrors._banner.get_title())
        self.assertTrue(mirrors._benchmark.get_sensitive())

    def test_catalogue_worker_exceptions_are_visible(self):
        from unittest.mock import patch

        for key, method in (
            ("installed", "get_installed_packages"),
            ("updates", "get_upgradable_packages"),
        ):
            page = self.window._pages[key]
            with patch.object(
                page.apt_manager, method, side_effect=RuntimeError("Fixture failure")
            ):
                page._fetch(page._generation)
            self.spin(lambda page=page: page._stack.get_visible_child_name() == "error")
            self.assertIn("Fixture failure", page._error.get_description())
        page = self.window._pages["browse"]
        page._entry.set_text("fixture")
        with patch.object(
            page.apt_manager, "search_packages", side_effect=RuntimeError("Search fixture failure")
        ):
            page._search("fixture", page._generation)
        self.spin(lambda page=page: page._stack.get_visible_child_name() == "error")
        self.assertIn("Search fixture failure", page._error.get_description())
        page._entry.set_text("")  # Cancel the pending debounced real search.

    def test_kept_back_can_be_reviewed_but_explicit_hold_cannot(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["updates"]
        page._make_row(PackageInfo(name="dependency-change", is_held_back=True))
        page._make_row(PackageInfo(name="held", is_held=True, is_held_back=True))
        self.assertEqual(
            page._row_widgets["dependency-change"]["button"].get_label(), "Review upgrade"
        )
        self.assertIsNone(page._row_widgets["held"]["button"])

    def test_search_limit_is_explicit(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["browse"]
        page._entry.set_text("sample")
        page._show_results(
            "sample", page._generation, [PackageInfo(name=f"sample-{i}") for i in range(200)], None
        )
        self.assertIn("first 200", page._status.get_label())

    def test_warning_is_visible_after_success(self):
        import json

        events = [
            {"event": "warning", "message": "A repository was skipped"},
            {"event": "complete"},
        ]
        command = [sys.executable, "-c", f"print({chr(10).join(json.dumps(e) for e in events)!r})"]
        dialog = OperationDialog("Warnings", command)
        dialog.present(self.window)
        self.spin(lambda: dialog.view._finished)
        self.assertEqual(dialog.view._phase.get_label(), "Completed with warnings")
        self.assertIn("repository was skipped", dialog.view._status.get_label())
        self.assertTrue(dialog.view._phase.has_css_class("warning"))
        self.assertTrue(dialog.view._details.get_expanded())
        if dialog.get_mapped():
            dialog.close()

    def test_error_event_cannot_be_overridden_by_success_exit(self):
        import json

        events = [{"event": "error", "message": "Partial failure"}, {"event": "complete"}]
        finished = []
        dialog = OperationDialog(
            "Error",
            [sys.executable, "-c", f"print({chr(10).join(json.dumps(e) for e in events)!r})"],
            on_done=finished.append,
        )
        dialog.present(self.window)
        self.spin(lambda: dialog.view._finished)
        self.assertEqual(finished, [False])
        self.assertEqual(dialog.view._phase.get_label(), "Operation failed")
        if dialog.get_mapped():
            dialog.close()

    def test_malformed_progress_does_not_disable_completion(self):
        import json

        events = [
            {"event": "progress", "percent": float("nan")},
            {"event": "progress", "bytes": 5},
            {"event": "complete"},
        ]
        dialog = OperationDialog(
            "Protocol failure",
            [sys.executable, "-c", f"print({chr(10).join(json.dumps(e) for e in events)!r})"],
        )
        dialog.present(self.window)
        self.spin(lambda: dialog.view._finished)
        self.assertTrue(dialog.view._close.get_sensitive())
        self.assertEqual(dialog.view._phase.get_label(), "Operation failed")
        self.assertIn("Invalid helper", dialog.view._status.get_label())
        if dialog.get_mapped():
            dialog.close()

    def test_short_window_keeps_inline_review_scrollable(self):
        import json

        plan = {
            "event": "plan",
            "changes": [
                {
                    "name": "example:amd64",
                    "action": "upgrade",
                    "old_version": "1",
                    "new_version": "2",
                }
            ],
            "download_bytes": 1,
            "disk_bytes": 1,
        }
        script = f'print({json.dumps(plan)!r}, flush=True); input(); print(\'{{"event":"cancelled"}}\',flush=True)'
        page = self.window._pages["updates"]
        self.window.set_default_size(760, 480)
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(1))
        page.start_operation("Review", [sys.executable, "-c", script])
        self.spin(lambda: page._operation._review.get_visible())
        scroll = page._operation_slot.get_ancestor(Gtk.ScrolledWindow)
        self.assertIsInstance(scroll, Gtk.ScrolledWindow)
        self.spin(lambda: scroll.get_height() > 0)
        self.assertLessEqual(scroll.get_height(), 360)
        adjustment = scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        page._operation._respond(False)
        self.spin(lambda: page._operation is None)
        self.assertFalse(self.window._operation_active)

    def test_stale_search_results_do_not_replace_new_query(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["browse"]
        page._entry.set_text("newquery")
        generation = page._generation
        page._show_results("newquery", generation, [PackageInfo(name="new-result")], None)
        page._show_results("oldquery", generation - 1, [PackageInfo(name="old-result")], None)
        self.assertEqual(page._list.get_first_child().get_title(), "new-result")

    def test_stress_progress_flood_and_unterminated_log(self):
        script = "import json,sys; sys.stderr.write('x'*200000); sys.stderr.flush(); [print(json.dumps({'event':'progress','phase':'Testing','percent':i%101})) for i in range(2000)]; print(json.dumps({'event':'complete'}))"
        dialog = OperationDialog("Stress", [sys.executable, "-c", script])
        dialog.present(self.window)
        self.spin(lambda: dialog.view._finished)
        self.assertEqual(dialog.view._phase.get_label(), "Completed")
        self.assertLessEqual(len(dialog.view._log_tail), 65536)
        self.assertTrue(dialog.view._close.get_sensitive())
        if dialog.get_mapped():
            dialog.close()

    def test_success_dialog_dismisses_with_temporary_message(self):
        from unittest.mock import patch

        dialog = OperationDialog(
            "Install",
            [
                sys.executable,
                "-c",
                "import json; print(json.dumps(dict(event='complete')), flush=True)",
            ],
        )
        closed = []
        dialog.connect("closed", lambda *_: closed.append(True))
        with patch.object(self.window, "show_toast") as toast:
            dialog.present(self.window)
            self.spin(lambda: bool(closed))
            toast.assert_called_once_with("Operation completed")
        self.assertFalse(dialog.view._progress.get_visible())

    def test_authorization_cancel_returns_to_updates_without_close_button(self):
        page = self.window._pages["updates"]
        page.start_operation("Refresh", [sys.executable, "-c", "raise SystemExit(126)"])
        self.spin(lambda: page._operation is None)
        self.assertFalse(self.window._operation_active)
        self.assertIsNone(page._operation_slot.get_first_child())

    def test_download_finished_bar_hides_and_install_stage_returns(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["updates"]
        page._list.append(page._make_row(PackageInfo(name="example", full_name="example:amd64")))
        row = page._row_widgets["example:amd64"]
        page._operation_event(
            {
                "event": "package-progress",
                "package": "example:amd64",
                "message": "Downloaded",
                "percent": 100,
            }
        )
        self.assertFalse(row["bar"].get_visible())
        self.assertTrue(row["status"].get_visible())
        page._operation_event(
            {"event": "progress", "package": "example:amd64", "message": "Unpacking", "percent": 50}
        )
        self.assertTrue(row["bar"].get_visible())
        page._operation_event(
            {
                "event": "progress",
                "package": "example:amd64",
                "message": "Installed example",
                "stage_complete": True,
            }
        )
        self.assertFalse(row["bar"].get_visible())
        self.assertNotIn("example:amd64", page._pulsing)

    def test_package_details_actions_and_hold_guard(self):
        from orbit_gtk.backend.models import PackageInfo
        from orbit_gtk.ui.package_dialog import PackageDialog

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        actions = []
        dialog = PackageDialog(
            PackageInfo(name="example", is_installed=True), on_action=actions.append
        )
        dialog.present(self.window)
        rows = {
            w.get_title(): w
            for w in descendants(dialog.get_child())
            if isinstance(w, Adw.ActionRow)
        }
        self.assertNotIn("Remove and purge configuration", rows)
        self.assertFalse(dialog._purge_configs.get_active())
        dialog._purge_configs.set_active(True)
        button = next(w for w in descendants(rows["Remove"]) if isinstance(w, Gtk.Button))
        button.emit("clicked")
        self.assertEqual(actions, ["purge"])
        held = PackageDialog(
            PackageInfo(name="held", is_installed=True, is_held=True), on_action=actions.append
        )
        rows = {
            w.get_title() for w in descendants(held.get_child()) if isinstance(w, Adw.ActionRow)
        }
        self.assertIn("Held by APT", rows)
        self.assertNotIn("Remove", rows)

    def test_search_shortcut_routes_and_focuses(self):
        self.window.lookup_action("search").activate(None)
        self.assertEqual(self.window._stack.get_visible_child_name(), "browse")
        self.assertTrue(self.window.get_focus().is_ancestor(self.window._pages["browse"]._entry))
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(3))
        self.window.lookup_action("search").activate(None)
        self.assertEqual(self.window._stack.get_visible_child_name(), "installed")
        self.assertTrue(self.window.get_focus().is_ancestor(self.window._pages["installed"]._entry))

    def test_cleanup_autoremove_and_details_reinstall_routing(self):
        from unittest.mock import patch

        from orbit_gtk.backend.models import PackageInfo
        from orbit_gtk.ui.package_dialog import PackageDialog

        with patch.object(self.window, "run_privileged") as run:
            self.window._pages["cleanup"]._on_autoremove(None)
            self.assertEqual(run.call_args.args[1][-1], "autoremove")

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        actions = []
        dialog = PackageDialog(
            PackageInfo(name="example", is_installed=True), on_action=actions.append
        )
        dialog.present(self.window)
        row = next(
            w
            for w in descendants(dialog.get_child())
            if isinstance(w, Adw.ActionRow) and w.get_title() == "Reinstall current version"
        )
        next(w for w in descendants(row) if isinstance(w, Gtk.Button)).emit("clicked")
        self.assertEqual(actions, ["reinstall"])

    def test_cleanup_failure_retry_and_stale_results(self):
        from unittest.mock import MagicMock

        from orbit_gtk.ui.pages.cleanup import CleanupPage

        manager = MagicMock()
        manager.get_cleanup_items.side_effect = PermissionError("Access denied")
        page = CleanupPage(manager, self.window)
        page.load_data()
        self.spin(lambda: page._retry.get_visible())
        self.assertFalse(page._spinner.get_spinning())
        self.assertFalse(page._clean_btn.get_sensitive())
        self.assertIn("Access denied", page._total_row.get_subtitle())
        old_generation = page._generation
        manager.get_cleanup_items.side_effect = None
        manager.get_cleanup_items.return_value = []
        page._retry.emit("clicked")
        self.spin(lambda: page._data_ready)
        self.assertEqual(page._total_row.get_title(), "Nothing to clean")
        page._fetch_failed(old_generation, "Stale error")
        self.assertFalse(page._retry.get_visible())
        self.assertEqual(page._total_row.get_title(), "Nothing to clean")

    def test_real_pages_and_search(self):
        home = self.window._pages["home"]
        self.spin(lambda: home._row_installed.get_subtitle() not in {"—", "Loading…"})
        self.assertIn("packages", home._row_installed.get_subtitle())
        for key in ("updates", "installed", "history", "cleanup", "mirrors"):
            page = self.window._pages[key]
            page.load_data()
        installed = self.window._pages["installed"]
        self.spin(lambda: installed._stack.get_visible_child_name() != "loading")
        self.assertEqual(installed._stack.get_visible_child_name(), "list")
        browse = self.window._pages["browse"]
        browse._entry.set_text("bash")
        self.spin(lambda: browse._stack.get_visible_child_name() == "results")
        self.assertEqual(browse._list.get_first_child().get_title(), "bash")
        if os.environ.get("ORBIT_SCREENSHOT"):
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_SCREENSHOT"]], check=True
            )

        if os.environ.get("ORBIT_UPDATES_SCREENSHOT"):
            self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(1))
            start = time.monotonic()
            self.spin(lambda: time.monotonic() - start > 0.4)
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_UPDATES_SCREENSHOT"]], check=True
            )

    def test_review_handshake_and_fragmented_unicode_progress(self):
        # A test child exercises actual pipes and framing, including split UTF-8.
        script = """import json, os, sys, time
print(json.dumps({"event":"plan","changes":[{"name":"example:amd64","action":"upgrade","old_version":"1","new_version":"2"}],"download_bytes":1024,"disk_bytes":2048}),flush=True)
assert input() == "apply"
data=json.dumps({"event":"progress","phase":"Installing","package":"example:amd64","message":"Unpacking café…","percent":42},ensure_ascii=False).encode()+b"\\n"
for byte in data: os.write(1,bytes([byte]))
input()
print(json.dumps({"event":"complete"}),flush=True)
"""
        finished = []
        dialog = OperationDialog(
            "Test transaction", [sys.executable, "-c", script], on_done=finished.append
        )
        dialog.present(self.window)
        self.spin(lambda: dialog.view._review.get_visible())
        self.assertIn("example:amd64", dialog.view._changes._model.get_string(0))
        self.assertFalse(dialog.get_can_close())
        dialog.view._respond(True)
        self.spin(lambda: dialog.view._status.get_label() == "Unpacking café…")
        self.assertAlmostEqual(dialog.view._progress.get_fraction(), 0.42)
        self.assertTrue(dialog.view._changes.get_visible())
        self.assertEqual(
            dialog.view._changes._progress_state["example:amd64"], ("Unpacking café…", None)
        )
        dialog.view._proc.stdin.write("finish\n")
        dialog.view._proc.stdin.flush()
        self.spin(lambda: bool(finished))
        self.assertEqual(finished, [True])
        if dialog.get_mapped():
            dialog.close()

    def test_large_plan_filters_keep_full_review_and_removal_warning(self):
        script = """import json
changes=[dict(name=f'package-{i:05}:amd64', action='remove' if i%2 else 'install', old_version='1', new_version=None if i%2 else '2') for i in range(10000)]
print(json.dumps(dict(event='plan', changes=changes, download_bytes=1024, disk_bytes=-2048)), flush=True)
assert input() == 'cancel'
print(json.dumps(dict(event='cancelled')), flush=True)
raise SystemExit(2)
"""
        dialog = OperationDialog("Large dependency plan", [sys.executable, "-c", script])
        dialog.present(self.window)
        self.spin(lambda: dialog.view._review.get_visible())
        plan = dialog.view._changes
        self.assertEqual(plan._model.get_n_items(), 10000)
        self.assertTrue(dialog.view._apply.has_css_class("destructive-action"))
        self.assertIn("removes packages", dialog.view._status.get_label())
        self.assertIsNone(dialog.view.footer.get_ancestor(Gtk.ScrolledWindow))
        self.assertFalse(dialog.view._progress.get_visible())
        # GTK only constructs a bounded set of row widgets, despite 10k changes.
        self.spin(lambda: plan._list.get_height() > 0)
        children = 0
        child = plan._list.get_first_child()
        while child:
            children += 1
            child = child.get_next_sibling()
        self.assertLess(children, 1000)
        if os.environ.get("ORBIT_PLAN_SCREENSHOT"):
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_PLAN_SCREENSHOT"]], check=True
            )
        plan._action_filter.set_selected(1)  # Remove, ordered before Install.
        self.assertEqual(plan._model.get_n_items(), 5000)
        plan._search.set_text("package-00001")
        self.spin(lambda: plan._model.get_n_items() == 1)
        self.assertEqual(plan._filtered[0]["action"], "remove")
        self.assertIn("entire plan", plan._result_count.get_label())
        self.assertEqual(len(plan._changes), 10000)
        plan._search.set_text("no-matching-package")
        self.spin(lambda: plan._model.get_n_items() == 0)
        self.assertIn("No matching", plan._result_count.get_label())
        dialog.view._respond(False)
        self.spin(lambda: dialog.view._finished)
        self.assertTrue(dialog.view._cancelled)
        if dialog.get_mapped():
            dialog.close()

    def test_unknown_plan_action_is_rejected_before_page_callback(self):
        from orbit_gtk.ui.operation_view import OperationView

        received = []
        script = """import json
print(json.dumps(dict(event='plan',changes=[dict(name='example',action='erase',old_version='1',new_version=None)],download_bytes=0,disk_bytes=0)),flush=True)
assert input() == 'cancel'
raise SystemExit(2)
"""
        view = OperationView(
            "Invalid plan", [sys.executable, "-c", script], on_event=received.append
        )
        self.spin(lambda: view._finished)
        self.assertTrue(view._error)
        self.assertFalse(view._apply.get_visible())
        self.assertFalse(any(event["event"] == "plan" for event in received))

    def test_failed_child_does_not_report_success_or_full_progress(self):
        finished = []
        script = 'import json; print(json.dumps({"event":"error","message":"Download failed"})); raise SystemExit(1)'
        dialog = OperationDialog(
            "Failure test", [sys.executable, "-c", script], on_done=finished.append
        )
        dialog.present(self.window)
        self.spin(lambda: bool(finished))
        self.assertEqual(finished, [False])
        self.assertEqual(dialog.view._progress.get_fraction(), 0)
        self.assertEqual(dialog.view._status.get_label(), "Download failed")
        if dialog.get_mapped():
            dialog.close()

    def test_cancelled_plan_is_not_committed(self):
        script = """import json
print(json.dumps({"event":"plan","changes":[{"name":"example","action":"remove","old_version":"1","new_version":None}],"download_bytes":0,"disk_bytes":0}),flush=True)
assert input() == "cancel"
print(json.dumps({"event":"cancelled"}),flush=True)
raise SystemExit(2)
"""
        finished = []
        dialog = OperationDialog(
            "Cancel test", [sys.executable, "-c", script], on_done=finished.append
        )
        dialog.present(self.window)
        self.spin(lambda: dialog.view._review.get_visible())
        dialog.view._respond(False)
        self.spin(lambda: bool(finished))
        self.assertEqual(dialog.view._phase.get_label(), "Cancelled")
        self.assertEqual(finished, [False])
        if dialog.get_mapped():
            dialog.close()

    def test_upgrades_stay_inline_with_independent_package_bars(self):
        from orbit_gtk.backend.models import PackageInfo

        page = self.window._pages["updates"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(1))
        page._generation += 1
        page._apply(
            page._generation,
            [
                PackageInfo(
                    name="example",
                    full_name="example:amd64",
                    installed_version="1",
                    latest_version="2",
                ),
                PackageInfo(
                    name="example:i386",
                    full_name="example:i386",
                    installed_version="1",
                    latest_version="2",
                ),
            ],
            None,
        )
        script = """import json
changes=[{"name":name,"action":"upgrade","old_version":"1","new_version":"2"} for name in ("example:amd64","example:i386")]
print(json.dumps({"event":"plan","changes":changes,"download_bytes":4096,"disk_bytes":0}),flush=True)
assert input() == "apply"
for package,percent in (("example:amd64",25),("example:i386",75)):
 print(json.dumps({"event":"package-progress","package":package,"message":"Downloading","percent":percent}),flush=True)
input()
print(json.dumps({"event":"progress","phase":"Installing","package":"example:amd64","message":"Unpacking example","percent":90}),flush=True)
input()
print(json.dumps({"event":"complete"}),flush=True)
"""
        page.start_operation("Upgrade test", [sys.executable, "-c", script])
        self.spin(lambda: page._operation._review.get_visible())
        self.assertIsNone(self.window.get_visible_dialog())
        self.assertIs(page._operation.get_parent(), page._operation_slot)
        page._operation._respond(True)
        first, second = page._row_widgets["example:amd64"], page._row_widgets["example:i386"]
        self.spin(lambda: second["bar"].get_fraction() == 0.75)
        self.assertEqual(first["bar"].get_fraction(), 0.25)
        page._operation._proc.stdin.write("next\n")
        page._operation._proc.stdin.flush()
        self.spin(lambda: first["status"].get_label() == "Unpacking example")
        self.assertIn("example:amd64", page._pulsing)
        self.assertEqual(second["bar"].get_fraction(), 0.75)
        if os.environ.get("ORBIT_PROGRESS_SCREENSHOT"):
            start = time.monotonic()
            self.spin(lambda: time.monotonic() - start > 0.4)
            subprocess.run(
                ["import", "-window", "root", os.environ["ORBIT_PROGRESS_SCREENSHOT"]], check=True
            )
        page._operation._proc.stdin.write("finish\n")
        page._operation._proc.stdin.flush()
        self.spin(lambda: not page._running)
        self.assertEqual(first["status"].get_label(), "Completed")
        self.assertEqual(first["bar"].get_fraction(), 1)
        self.assertEqual(second["bar"].get_fraction(), 1)
        self.assertIsNone(page._operation)
        self.assertFalse(first["bar"].get_visible())
        self.assertFalse(self.window._operation_active)

    def test_catalogue_controls_lazy_scroll_and_history_colors(self):
        from orbit_gtk.backend.models import HistoryTransaction, PackageInfo

        browse = self.window._pages["browse"]
        buttons = []
        for installed in (False, True):
            row = browse._make_row(PackageInfo(name="sample", is_installed=installed))

            def walk(widget):
                if widget.get_name() == "package-action":
                    buttons.append(widget)
                child = widget.get_first_child()
                while child:
                    walk(child)
                    child = child.get_next_sibling()

            walk(row)
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0].get_size_request(), buttons[1].get_size_request())
        self.assertTrue(all(button.get_valign() == Gtk.Align.CENTER for button in buttons))
        updates = self.window._pages["updates"]
        updates._make_row(PackageInfo(name="sample", full_name="sample:amd64"))
        self.assertFalse(updates._row_widgets["sample:amd64"]["progress_box"].get_visible())
        page = self.window._pages["installed"]
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(3))
        self.spin(lambda: page._stack.get_visible_child_name() == "list")
        self.assertEqual(page._model.get_n_items(), len(page._packages))
        self.spin(lambda: page._adjustment.get_upper() > page._adjustment.get_page_size())
        page._adjustment.set_value(page._adjustment.get_upper() - page._adjustment.get_page_size())
        self.assertIsInstance(page._list, Gtk.ListView)
        history = self.window._pages["history"]
        for operation, color in (
            ("install", "success"),
            ("remove", "warning"),
            ("purge", "error"),
            ("update", "accent"),
        ):
            row = history._make_row(
                HistoryTransaction("test", "2026-01-01", "test", "test", operation, 1)
            )

            def has_color(widget, color=color):
                if widget.has_css_class(color):
                    return True
                child = widget.get_first_child()
                while child:
                    if has_color(child):
                        return True
                    child = child.get_next_sibling()
                return False

            self.assertTrue(has_color(row))

    def test_package_details_and_narrow_navigation(self):
        from orbit_gtk.backend.models import PackageInfo
        from orbit_gtk.ui.package_dialog import PackageDialog

        dialog = PackageDialog(
            PackageInfo(
                name="example", summary="Tools & utilities", description="Plain <text> & metadata"
            )
        )
        dialog.present(self.window)
        self.spin(lambda: dialog.get_mapped())
        if dialog.get_mapped():
            dialog.close()
        self.window.set_default_size(600, 720)
        self.spin(lambda: self.window._split.get_collapsed())
        self.window._split.set_show_content(False)
        self.window._nav_list.select_row(self.window._nav_list.get_row_at_index(2))
        self.assertTrue(self.window._split.get_show_content())
        self.assertEqual(self.window._stack.get_visible_child_name(), "browse")

    def test_window_blocks_close_during_operation(self):
        self.window._operation_active = True
        self.assertTrue(self.window._on_close_request(self.window))


if __name__ == "__main__":
    unittest.main()
