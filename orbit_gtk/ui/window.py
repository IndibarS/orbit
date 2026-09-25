"""The responsive Adwaita application window and transaction coordinator."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.ui.operation_dialog import OperationDialog
from orbit_gtk.ui.pages.browse import BrowsePage
from orbit_gtk.ui.pages.cleanup import CleanupPage
from orbit_gtk.ui.pages.history import HistoryPage
from orbit_gtk.ui.pages.home import HomePage
from orbit_gtk.ui.pages.installed import InstalledPage
from orbit_gtk.ui.pages.mirrors import MirrorsPage
from orbit_gtk.ui.pages.updates import UpdatesPage


class OrbitWindow(Adw.ApplicationWindow):
    """Own page navigation and serialize system-changing package operations."""

    def __init__(self, apt_manager: AptManager, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.apt_manager = apt_manager
        self._operation_active = False
        self._refresh_generation = 0
        self.connect("close-request", self._on_close_request)
        self.set_title("Orbit Package Manager")
        self.set_default_size(1100, 720)
        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", lambda *_: self.focus_search())
        self.add_action(search_action)
        self.get_application().set_accels_for_action("win.search", ["<Control>f"])

        self._toasts = Adw.ToastOverlay()
        self.set_content(self._toasts)
        split = Adw.NavigationSplitView()
        self._split = split
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 800sp"))
        breakpoint.add_setter(split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        split.set_min_sidebar_width(200)
        split.set_max_sidebar_width(260)
        self._toasts.set_child(split)

        sidebar = Adw.NavigationPage(title="Orbit", tag="sidebar")
        sidebar_toolbar = Adw.ToolbarView()
        sidebar.set_child(sidebar_toolbar)
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_toolbar.add_top_bar(sidebar_header)
        sidebar_scroll = Gtk.ScrolledWindow(vexpand=True)
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_toolbar.set_content(sidebar_scroll)
        self._nav_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._nav_list.add_css_class("navigation-sidebar")
        sidebar_scroll.set_child(self._nav_list)

        sidebar_footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sidebar_footer.set_margin_start(12)
        sidebar_footer.set_margin_end(12)
        sidebar_footer.set_margin_bottom(12)
        self._system_name = Gtk.Label(xalign=0)
        self._system_name.add_css_class("heading")
        self._system_name.add_css_class("caption")
        self._system_detail = Gtk.Label(xalign=0, wrap=True)
        self._system_detail.add_css_class("caption")
        self._system_detail.add_css_class("dim-label")
        sidebar_footer.append(self._system_name)
        sidebar_footer.append(self._system_detail)
        sidebar_toolbar.add_bottom_bar(sidebar_footer)
        split.set_sidebar(sidebar)

        self._content_page = Adw.NavigationPage(title="Orbit Package Manager", tag="content")
        content_toolbar = Adw.ToolbarView()
        self._content_page.set_child(content_toolbar)
        self._header = Adw.HeaderBar()
        refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text="Refresh package lists"
        )
        refresh_button.connect("clicked", self._on_refresh_lists)
        self._header.pack_end(refresh_button)
        content_toolbar.add_top_bar(self._header)
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        content_toolbar.set_content(self._stack)
        split.set_content(self._content_page)

        self._pages: dict[str, Gtk.Widget] = {
            "home": HomePage(self.apt_manager, self),
            "updates": UpdatesPage(self.apt_manager, self),
            "browse": BrowsePage(self.apt_manager, self),
            "installed": InstalledPage(self.apt_manager, self),
            "history": HistoryPage(self.apt_manager, self),
            "cleanup": CleanupPage(self.apt_manager, self),
            "mirrors": MirrorsPage(self.apt_manager, self),
        }
        self._titles = {
            "home": "Home",
            "updates": "Updates",
            "browse": "Browse",
            "installed": "Installed",
            "history": "History",
            "cleanup": "Cleanup",
            "mirrors": "Mirrors",
        }
        self._badges: dict[str, Gtk.Label] = {}
        items = (
            ("go-home-symbolic", "Home", "home"),
            ("software-update-available-symbolic", "Updates", "updates"),
            ("edit-find-symbolic", "Browse", "browse"),
            ("emblem-ok-symbolic", "Installed", "installed"),
            ("document-open-recent-symbolic", "History", "history"),
            ("user-trash-symbolic", "Cleanup", "cleanup"),
            ("network-server-symbolic", "Mirrors", "mirrors"),
        )
        for icon, title, key in items:
            row = self._make_navigation_row(icon, title, key)
            self._nav_list.append(row)
            self._stack.add_named(self._pages[key], key)
        self._nav_list.connect("row-selected", self._on_navigation_selected)
        self._nav_list.select_row(self._nav_list.get_row_at_index(0))
        threading.Thread(
            target=self._load_system_summary, name="orbit-system-summary", daemon=True
        ).start()

    def _make_navigation_row(self, icon_name: str, title: str, key: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(name=key)
        box = Gtk.Box(spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        badge = Gtk.Label()
        badge.add_css_class("badge")
        badge.set_visible(False)
        self._badges[key] = badge
        box.append(icon)
        box.append(label)
        box.append(badge)
        row.set_child(box)
        return row

    def _on_navigation_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        key = row.get_name()
        if key not in self._pages:
            return
        self._stack.set_visible_child_name(key)
        self._split.set_show_content(True)
        page = self._pages[key]
        load = getattr(page, "load_data", None)
        if load:
            load()
        title = self._titles[key]
        self._content_page.set_title(title)
        self._header.set_title_widget(Adw.WindowTitle(title=title))

    def focus_search(self) -> None:
        key = self._stack.get_visible_child_name()
        if key not in {"browse", "installed"}:
            key = "browse"
            self._nav_list.select_row(self._nav_list.get_row_at_index(2))
        self._split.set_show_content(True)
        self._pages[key]._entry.grab_focus()

    def _on_refresh_lists(self, _button: Gtk.Button) -> None:
        if self._stack.get_visible_child_name() == "updates":
            self._pages["updates"]._on_refresh(_button)
        else:
            self.run_privileged("Refresh package lists", self.apt_manager.helper_command("update"))

    def claim_operation(self) -> bool:
        if self._operation_active:
            self.show_toast("Finish the current package operation first")
            return False
        self._operation_active = True
        return True

    def release_operation(self) -> None:
        self._operation_active = False

    def show_upgrades(self) -> None:
        row = self._nav_list.get_first_child()
        while row:
            if row.get_name() == "updates":
                self._nav_list.select_row(row)
                break
            row = row.get_next_sibling()
        self._pages["updates"]._on_upgrade_all(None)

    def confirm_and_run(
        self,
        title: str,
        body: str,
        command: list[str],
        *,
        destructive: bool = False,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        """Require an explicit user decision before a state-changing action."""
        # Package actions obtain an authoritative dependency plan in the helper.
        if any(
            name in command
            for name in (
                "install",
                "remove",
                "purge",
                "reinstall",
                "upgrade",
                "full-upgrade",
                "autoremove",
            )
        ):
            self.run_privileged(title, command, on_done=on_done)
            return
        if self._operation_active:
            self.show_toast("Another package operation is already running")
            return
        dialog = Adw.AlertDialog.new(title, body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("continue", "Continue")
        dialog.set_response_appearance(
            "continue",
            Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def chosen(alert: Adw.AlertDialog, result: object) -> None:
            try:
                response = alert.choose_finish(result)
            except GLib.Error:
                return
            if response == "continue":
                self.run_privileged(title, command, on_done=on_done)

        dialog.choose(self, None, chosen)

    def run_privileged(
        self,
        title: str,
        command: list[str],
        *,
        on_done: Callable[[bool], None] | None = None,
    ) -> None:
        """Run one root-helper command, preventing concurrent APT/dpkg operations."""
        if not self.claim_operation():
            return

        def completed(success: bool) -> None:
            self.refresh_all()  # A failed dpkg run may still have changed packages.
            if on_done:
                on_done(success)

        dialog = OperationDialog(title, command, on_done=completed)

        def closed(_dialog):
            self.release_operation()

        dialog.connect("closed", closed)
        dialog.present(self)

    def _on_close_request(self, _window) -> bool:
        if self._operation_active:
            self.show_toast("Finish and close the package operation before quitting Orbit")
            return True
        mirrors = self._pages.get("mirrors")
        if mirrors and mirrors._worker:
            mirrors._worker.stop()
        return False

    def show_package_details(self, package) -> None:
        from orbit_gtk.ui.package_dialog import PackageDialog

        def act(action):
            title = {
                "install": "Install or upgrade",
                "remove": "Remove",
                "purge": "Purge",
                "reinstall": "Reinstall",
            }[action]
            self.run_privileged(
                f"{title} {package.name}",
                self.apt_manager.helper_command(action, package.full_name or package.name),
            )

        PackageDialog(package, on_action=act).present(self)

    def show_toast(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message))

    def refresh_all(self) -> None:
        """Reload APT off the UI thread and invalidate data pages once the snapshot is ready."""
        self._refresh_generation += 1
        generation = self._refresh_generation
        threading.Thread(
            target=self._refresh_worker,
            args=(generation,),
            name="orbit-cache-refresh",
            daemon=True,
        ).start()

    def _refresh_worker(self, generation: int) -> None:
        self.apt_manager.reload_cache()
        count = len(self.apt_manager.get_upgradable_packages())
        GLib.idle_add(self._apply_refresh, generation, count)

    def _apply_refresh(self, generation: int, upgrades: int) -> bool:
        if generation != self._refresh_generation:
            return False
        self.set_updates_badge(upgrades)
        for page in self._pages.values():
            invalidate = getattr(page, "invalidate", None)
            if invalidate:
                invalidate()
        return False

    def _load_system_summary(self) -> None:
        info = self.apt_manager.get_system_info()
        upgrades = len(self.apt_manager.get_upgradable_packages())
        GLib.idle_add(self._apply_system_summary, info, upgrades)

    def _apply_system_summary(self, info: dict[str, str], upgrades: int) -> bool:
        self._system_name.set_label(info.get("distro_name", "Linux"))
        self._system_detail.set_label(
            f"{info.get('suite', 'unknown')} · {info.get('kernel', '')} · {info.get('arch', '')}"
        )
        self.set_updates_badge(upgrades)
        return False

    def set_updates_badge(self, count: int) -> None:
        badge = self._badges.get("updates")
        if badge is None:
            return
        badge.set_visible(count > 0)
        if count > 0:
            badge.set_label(str(count))
