"""Debounced package search with stale-result protection."""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import PackageInfo
from orbit_gtk.i18n import tr
from orbit_gtk.ui.widgets import action_row, package_icon


class BrowsePage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._generation = 0
        self._limit = 200
        self._search_options = {}
        self._debounce_id: int | None = None

        search_box = Gtk.Box()
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(12)
        search_box.set_margin_bottom(8)
        self._entry = Gtk.SearchEntry(hexpand=True, placeholder_text=tr("Search packages…"))
        self._entry.connect("changed", self._on_search_changed)
        search_box.append(self._entry)
        selection = Gtk.Button(label=tr("Selected changes"))
        selection.connect("clicked", self.window.show_selection)
        search_box.append(selection)
        self.append(search_box)
        filters = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_bottom=8)
        self._mode = Gtk.DropDown(model=Gtk.StringList.new(["Text", "Glob", "Regex"]))
        self._filter = Gtk.DropDown(
            model=Gtk.StringList.new(
                ["All packages", "Installed", "Upgradable", "Virtual packages"]
            )
        )
        self._names_only = Gtk.CheckButton(label=tr("Names only"))
        for control in (self._mode, self._filter, self._names_only):
            filters.append(control)
        self._mode.connect("notify::selected", lambda *_: self._on_search_changed(self._entry))
        self._filter.connect("notify::selected", lambda *_: self._on_search_changed(self._entry))
        self._names_only.connect("toggled", lambda *_: self._on_search_changed(self._entry))
        self.append(filters)
        local_group = Adw.PreferencesGroup()
        local_group.set_margin_start(12)
        local_group.set_margin_end(12)
        local_group.set_margin_bottom(8)
        local_row = action_row(
            title=tr("Install a downloaded package"),
            subtitle=tr("Choose a local .deb file, then review the package and its dependencies."),
        )
        local_row.add_prefix(Gtk.Image(icon_name="package-x-generic-symbolic", pixel_size=32))
        self._local_button = Gtk.Button(label=tr("Browse"), valign=Gtk.Align.CENTER)
        self._local_button.add_css_class("suggested-action")
        self._local_button.connect(
            "clicked", lambda button: self.window._choose_local_package(button)
        )
        local_row.add_suffix(self._local_button)
        batch = Gtk.Button(label=tr("Multiple…"), valign=Gtk.Align.CENTER)
        batch.connect("clicked", self.window._choose_local_packages)
        local_row.add_suffix(batch)
        remote = Gtk.Button(label=tr("From URL…"), valign=Gtk.Align.CENTER)

        def from_url(_):
            from orbit_gtk.ui.archive_download import ArchiveDownload

            ArchiveDownload(self.window).present(self.window)

        remote.connect("clicked", from_url)
        local_row.add_suffix(remote)
        local_group.add(local_row)
        self.append(local_group)

        self._status = Gtk.Label(label=tr("Type at least 2 characters"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("caption")
        self._status.add_css_class("dim-label")
        self.append(self._status)

        self._stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._placeholder = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title=tr("Search packages"),
            description=tr(
                "Search package names, summaries, and descriptions in the local APT cache."
            ),
        )
        self._stack.add_named(self._placeholder, "placeholder")
        self._error = Adw.StatusPage(
            icon_name="dialog-error-symbolic", title=tr("Search unavailable")
        )
        retry = Gtk.Button(label=tr("Retry search"), halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _: self.window.refresh_all())
        self._error.set_child(retry)
        self._stack.add_named(self._error, "error")
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, spinning=True)
        loading.append(spinner)
        loading.append(Gtk.Label(label=tr("Searching package data…")))
        self._stack.add_named(loading, "loading")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._scroll = scroll
        scroll.connect("edge-reached", self._load_more)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_top(4)
        self._list.set_margin_bottom(12)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        scroll.set_child(self._list)
        self._stack.add_named(scroll, "results")
        self.append(self._stack)
        self._stack.set_visible_child_name("placeholder")

    def load_data(self) -> None:
        """Search is intentionally on-demand."""

    def invalidate(self) -> bool:
        """Discard in-flight answers because an APT refresh changes the search index."""
        self._on_search_changed(self._entry)
        return False

    def _load_more(self, _scroll, position):
        if (
            position == Gtk.PositionType.BOTTOM
            and self._stack.get_visible_child_name() == "results"
            and getattr(self, "_has_more", False)
        ):
            self._has_more = False
            self._limit += 200
            self._begin_search(self._entry.get_text().strip(), self._generation)

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._limit = 200
        self._generation += 1
        generation = self._generation
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        query = self._entry.get_text().strip()
        self._clear_results()
        if len(query) < 2:
            self._placeholder.set_title(tr("Search packages"))
            self._placeholder.set_description(
                tr("Search package names, summaries, and descriptions in the local APT cache.")
            )
            self._status.set_label(tr("Type at least 2 characters"))
            self._stack.set_visible_child_name("placeholder")
            return
        self._status.set_label(tr("Searching…"))
        self._stack.set_visible_child_name("loading")
        self._debounce_id = GLib.timeout_add(250, self._begin_search, query, generation)

    def _begin_search(self, query: str, generation: int) -> bool:
        self._debounce_id = None
        self._search_options = {
            "mode": ("text", "glob", "regex")[self._mode.get_selected()],
            "status": ("all", "installed", "upgradable", "all")[self._filter.get_selected()],
            "virtual": self._filter.get_selected() == 3,
            "names_only": self._names_only.get_active(),
        }
        threading.Thread(
            target=self._search,
            args=(query, generation),
            name="orbit-package-search",
            daemon=True,
        ).start()
        return False

    def _search(self, query: str, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            results = self.apt_manager.search_packages(
                query,
                cancelled=lambda: generation != self._generation,
                limit=self._limit,
                options=self._search_options,
            )
            error = self.apt_manager.last_error
        except Exception as failure:
            results, error = [], str(failure) or type(failure).__name__
        GLib.idle_add(self._show_results, query, generation, results, error)

    def _show_results(
        self,
        query: str,
        generation: int,
        results: list[PackageInfo],
        error: str | None,
    ) -> bool:
        if generation != self._generation or self._entry.get_text().strip() != query:
            return False
        self._clear_results()
        if error:
            self._status.set_label(tr("Search failed"))
            self._error.set_description(error)
            self._stack.set_visible_child_name("error")
            return False
        self._has_more = len(results) == self._limit
        self._status.set_label(
            f"Showing first {self._limit} matches for “{query}” · scroll for more"
            if self._has_more
            else f"{len(results)} result{'s' if len(results) != 1 else ''} for “{query}”"
        )
        if not results:
            self._placeholder.set_title(tr("No packages found"))
            self._placeholder.set_description(tr("Try a shorter or different search term."))
            self._stack.set_visible_child_name("placeholder")
            return False
        for package in results:
            self._list.append(self._make_row(package))
        self._stack.set_visible_child_name("results")
        return False

    def _clear_results(self) -> None:
        while child := self._list.get_first_child():
            self._list.remove(child)

    def _make_row(self, package: PackageInfo) -> Adw.ActionRow:
        details = package.summary or "No summary available"
        if package.is_installed:
            details = f"Installed {package.installed_version or ''} · {details}".strip()
        row = action_row(title=package.name, subtitle=details[:180])
        row.set_activatable(True)
        row.connect("activated", lambda _: self.window.show_package_details(package))
        row.add_prefix(package_icon(package))
        if package.providers:
            control = Gtk.Button(label=tr("Providers"), valign=Gtk.Align.CENTER)
            control.connect("clicked", lambda _: self.window.show_package_details(package))
        elif package.is_installed:
            control = Gtk.Label(label=tr("Installed"), valign=Gtk.Align.CENTER)
            control.add_css_class("success")
            control.add_css_class("heading")
            control.set_tooltip_text(tr("This package is already installed"))
        else:
            control = Gtk.Button(label=tr("Install"), valign=Gtk.Align.CENTER)
            control.add_css_class("suggested-action")
            control.connect("clicked", self._on_install, package.full_name or package.name)
        control.set_name("package-action")
        control.set_size_request(104, 34)
        row.add_suffix(control)
        return row

    def _on_install(self, _button: Gtk.Button, package_name: str) -> None:
        self.window.confirm_and_run(
            f"Install {package_name}",
            "APT may install required dependencies. Orbit will show the resolved package changes before applying them.",
            self.apt_manager.helper_command("install", package_name),
        )
