"""The responsive Adwaita application window and transaction coordinator."""

from __future__ import annotations

import threading
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.i18n import tr
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
        self._downloaded_archives = []
        self._refresh_generation = 0
        self.connect("close-request", self._on_close_request)
        self.set_title(tr("Orbit Package Manager"))
        self.set_default_size(1100, 720)
        self.set_size_request(360, 360)
        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", lambda *_: self.focus_search())
        self.add_action(search_action)
        self.get_application().set_accels_for_action("win.search", ["<Control>f"])

        self._toasts = Adw.ToastOverlay()
        split = Adw.NavigationSplitView()
        self._split = split
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 800sp"))
        breakpoint.add_setter(split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        split.set_min_sidebar_width(200)
        split.set_max_sidebar_width(260)
        self.set_content(split)

        sidebar = Adw.NavigationPage(title=tr("Orbit"), tag="sidebar")
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

        self._content_page = Adw.NavigationPage(title=tr("Orbit Package Manager"), tag="content")
        content_toolbar = Adw.ToolbarView()
        self._content_page.set_child(content_toolbar)
        self._header = Adw.HeaderBar()
        refresh_button = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=tr("Refresh package lists")
        )
        refresh_button.connect("clicked", self._on_refresh_lists)
        self._header.pack_start(refresh_button)
        content_toolbar.add_top_bar(self._header)
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._toasts.set_child(self._stack)
        content_toolbar.set_content(self._toasts)
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

    def navigate(self, key):
        row = self._nav_list.get_first_child()
        while row:
            if row.get_name() == key:
                self._nav_list.select_row(row)
                return
            row = row.get_next_sibling()

    def dispatch_command(self, request):
        command = request.command
        if not command:
            return
        if self._operation_active:
            self.show_toast("Finish the current operation, then run the command again")
            return
        from orbit_gtk.backend.transaction_options import option_arguments

        extra = option_arguments(getattr(request, "options", {})) if command != "update" else []
        if command in {"update", "upgrade", "full-upgrade"}:
            self.navigate("updates")
            self._pages["updates"].start_operation(
                command.replace("-", " ").capitalize(),
                self.apt_manager.helper_command(command, *extra),
            )
        elif command in {
            "install",
            "remove",
            "purge",
            "reinstall",
            "autoremove",
            "autopurge",
            "purge-config",
            "fix-broken",
        }:
            self.navigate(
                "cleanup"
                if command in {"autoremove", "autopurge", "purge-config", "fix-broken"}
                else "browse"
                if command == "install"
                else "installed"
            )
            self.run_privileged(
                command.capitalize(),
                self.apt_manager.helper_command(command, *getattr(request, "packages", []), *extra),
            )
        elif command == "install-url":
            from orbit_gtk.ui.archive_download import ArchiveDownload

            ArchiveDownload(self, request.url, request.sha256).present(self)
        elif command == "install-batch":
            self.run_privileged(
                "Review local packages",
                self.apt_manager.helper_command(
                    "install-batch", "--paths", *request.paths, "--packages", *request.packages
                ),
            )
        elif command == "install-local":
            self.install_local(request.path)
        elif command in {"search", "list"}:
            page = (
                "browse"
                if command == "search"
                else "updates"
                if request.upgradable
                else "installed"
            )
            self.navigate(page)
            query = " ".join(request.query) if command == "search" else request.query
            if command == "search":
                browse = self._pages["browse"]
                browse._filter.set_selected(
                    ("all", "installed", "upgradable", "virtual").index(request.filter)
                )
                browse._names_only.set_active(request.names)
            if page != "updates":
                self._pages[page]._entry.set_text(query)
        elif command == "show":
            self.navigate("browse")

            def fetch():
                try:
                    package = self.apt_manager.get_package(request.package)
                    if package is None:
                        raise ValueError(f"Package not found: {request.package}")
                    GLib.idle_add(self.show_package_details, package)
                except Exception as error:
                    GLib.idle_add(self.show_toast, str(error))

            threading.Thread(target=fetch, daemon=True).start()
        elif command == "fetch":
            self.navigate("mirrors")
            mirrors = self._pages["mirrors"]
            mirrors._https_only.set_active(getattr(request, "https_only", False))
            mirrors._countries.set_text(" ".join(getattr(request, "country", [])))
            mirrors._sources.set_active(getattr(request, "sources", False))
            mirrors._count.set_value(getattr(request, "fetches", 3))
            mirrors._components_override.set_text(
                " ".join(getattr(request, "components", None) or [])
            )
            for index, provider in enumerate(
                ("debian", "ubuntu", "devuan", "linuxmint", "kali"), 1
            ):
                if getattr(request, provider, None):
                    mirrors._manual_provider.set_selected(index)
                    mirrors._suite_override.set_text(getattr(request, provider))
            mirrors._on_benchmark(None)
        elif command == "history":
            self.navigate("history")
            if getattr(request, "history_action", None) in {"undo", "redo"}:

                def load_history():
                    transactions = self.apt_manager.get_history(None)
                    transaction = (
                        next((t for t in transactions if t.id == request.history_id), None)
                        if request.history_id != "last"
                        else next((t for t in transactions if t.status == "Completed"), None)
                    )
                    if transaction:
                        GLib.idle_add(
                            self._pages["history"]._replay,
                            transaction,
                            request.history_action == "undo",
                        )
                    else:
                        GLib.idle_add(self.show_toast, "History entry not found")

                threading.Thread(target=load_history, daemon=True).start()
        elif command == "clean":
            self.navigate("cleanup")
            self.confirm_and_run(
                "Clean downloaded archives",
                "Remove cached .deb archives. Installed packages are kept.",
                self.apt_manager.helper_command("clean", "apt_cache"),
            )

    def install_local(self, path):
        self.navigate("browse")
        self.run_privileged(
            "Install local package", self.apt_manager.helper_command("install-local", path)
        )

    def _choose_local_package(self, _button):
        if self._operation_active:
            self.show_toast("Finish the current package operation first")
            return
        chooser = Gtk.FileDialog(
            title=tr("Install local Debian packages"), accept_label=tr("Review packages")
        )
        files = Gtk.FileFilter(name="Debian packages (.deb)")
        files.add_pattern("*.deb")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(files)
        chooser.set_filters(filters)

        def chosen(dialog, result):
            try:
                selected = dialog.open_multiple_finish(result)
                paths = [selected.get_item(i).get_path() for i in range(selected.get_n_items())]
                if not paths:
                    return
                if any(path is None for path in paths):
                    self.show_toast("Download the packages to a local folder first")
                    return
                if len(paths) == 1:
                    self.install_local(paths[0])
                else:
                    self.navigate("browse")
                    self.run_privileged(
                        "Review local packages",
                        self.apt_manager.helper_command("install-batch", "--paths", *paths),
                    )
            except GLib.Error as error:
                if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self.show_toast(f"Could not open the package chooser: {error.message}")

        chooser.open_multiple(self, None, chosen)

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
        def load():
            try:
                detail = self.apt_manager.get_package(package.full_name or package.name)
                GLib.idle_add(self._present_package_details, detail or package)
            except Exception as error:
                GLib.idle_add(self.show_toast, f"Could not load package details: {error}")

        threading.Thread(target=load, daemon=True).start()

    def _present_package_details(self, package):
        from orbit_gtk.backend.transaction_options import option_arguments
        from orbit_gtk.ui.package_dialog import PackageDialog

        def act(action, options=None):
            self.run_privileged(
                f"Review {action} · {package.name}",
                self.apt_manager.helper_command(
                    action, package.full_name or package.name, *option_arguments(options)
                ),
            )

        from orbit_gtk.backend.models import PackageInfo

        PackageDialog(
            package,
            on_action=act,
            on_related=lambda name: self.show_package_details(PackageInfo(name=name)),
        ).present(self)
        return False

    def show_toast(self, message: str, *, timeout: int = 5) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, timeout=timeout, use_markup=False))

    def show_warning_toast(self, warnings: list[str], count: int):
        """Keep the notification short; full messages are available on demand."""
        from orbit_gtk.ui.widgets import action_row

        toast = Adw.Toast(
            title=f"Completed with {count} warning{'s' if count != 1 else ''}",
            timeout=12,
            use_markup=False,
            button_label=tr("Details"),
        )
        messages = tuple(warnings)

        def show_details(_toast):
            dialog = Adw.Dialog(
                title=tr("Operation warnings"), content_width=620, content_height=420
            )
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup(
                description=(
                    f"Showing the latest {len(messages)} of {count} warnings."
                    if count > len(messages)
                    else "The operation completed with these warnings."
                )
            )
            for index, message in enumerate(messages, 1):
                row = action_row(title=f"Warning {index}", subtitle=message, subtitle_lines=0)
                icon = Gtk.Image(icon_name="dialog-warning-symbolic")
                icon.add_css_class("warning")
                row.add_prefix(icon)
                group.add(row)
            page.add(group)
            toolbar.set_content(page)
            dialog.set_child(toolbar)
            dialog.present(self)

        toast.connect("button-clicked", show_details)
        self._toasts.add_toast(toast)
        return toast

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
