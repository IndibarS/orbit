"""Installed catalogue with recycled rows and immutable package snapshots."""

from __future__ import annotations

import threading

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import PackageInfo
from orbit_gtk.i18n import tr
from orbit_gtk.ui.widgets import package_icon


class PackageItem(GObject.Object):
    """A row keeps its own snapshot while GTK rebinds and filters the model."""

    def __init__(self, package: PackageInfo):
        super().__init__()
        self.package = package


class InstalledPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._loaded = False
        self._generation = 0
        self._debounce_id: int | None = None
        self._data_ready = False
        self._packages: list[PackageInfo] = []
        self._filtered: list[PackageInfo] = []

        search_box = Gtk.Box()
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(12)
        self._entry = Gtk.SearchEntry(
            hexpand=True, placeholder_text=tr("Filter installed packages…")
        )
        self._entry.connect("search-changed", self._on_filter_changed)
        search_box.append(self._entry)
        self.append(search_box)
        self._summary = Gtk.Label(xalign=0)
        self._summary.set_margin_start(12)
        self._summary.set_margin_top(6)
        self._summary.set_margin_bottom(4)
        self._summary.add_css_class("caption")
        self._summary.add_css_class("dim-label")
        self.append(self._summary)

        self._stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        spinner = Gtk.Spinner(halign=Gtk.Align.CENTER)
        spinner.start()
        loading.append(spinner)
        loading.append(Gtk.Label(label=tr("Loading installed packages…")))
        self._stack.add_named(loading, "loading")
        self._error = Adw.StatusPage(
            icon_name="dialog-error-symbolic", title=tr("Could not load packages")
        )
        retry = Gtk.Button(label=tr("Retry loading"), halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _: self.window.refresh_all())
        self._error.set_child(retry)
        self._stack.add_named(self._error, "error")
        self._empty = Adw.StatusPage(
            icon_name="package-x-generic-symbolic", title=tr("No matching packages")
        )
        self._stack.add_named(self._empty, "empty")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._model = Gio.ListStore.new(PackageItem)
        selection = Gtk.SingleSelection(model=self._model, autoselect=False, can_unselect=True)
        factory = Gtk.SignalListItemFactory()
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", lambda _factory, item: item.set_child(None))
        self._list = Gtk.ListView(model=selection, factory=factory, single_click_activate=True)
        self._list.set_show_separators(True)
        self._list.connect("activate", self._activate)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_top(6)
        self._list.set_margin_bottom(12)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        scroll.set_child(self._list)
        self._adjustment = scroll.get_vadjustment()
        self._stack.add_named(scroll, "list")
        self.append(self._stack)

    def load_data(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._data_ready = False
        self._generation += 1
        generation = self._generation
        self._stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._fetch, args=(generation,), name="orbit-installed", daemon=True
        ).start()

    def invalidate(self) -> bool:
        self._loaded = False
        self.load_data()
        return False

    def _fetch(self, generation: int) -> None:
        try:
            packages = self.apt_manager.get_installed_packages()
            error = self.apt_manager.last_error
        except Exception as failure:
            packages, error = [], str(failure) or type(failure).__name__
        GLib.idle_add(self._apply, generation, packages, error)

    def _apply(self, generation: int, packages: list[PackageInfo], error: str | None) -> bool:
        if generation != self._generation:
            return False
        if error:
            self._data_ready = False
            self._summary.set_label(tr("Package data unavailable"))
            self._error.set_description(error)
            self._stack.set_visible_child_name("error")
            return False
        self._data_ready = True
        self._packages = packages
        self._apply_filter()
        return False

    def _on_filter_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(150, self._apply_filter_debounced)

    def _apply_filter_debounced(self) -> bool:
        self._debounce_id = None
        self._apply_filter()
        return False

    def _apply_filter(self) -> None:
        if not self._data_ready:
            return
        query = self._entry.get_text().strip().casefold()
        self._filtered = [
            package
            for package in self._packages
            if not query or query in package.name.casefold() or query in package.summary.casefold()
        ]
        self._render()
        if self._filtered:
            # GTK retains its old scroll anchor while relaying out a replacement
            # model. Request a model position instead of racing its adjustment.
            self._list.scroll_to(0, Gtk.ListScrollFlags(0), None)

    def _render(self) -> None:
        self._model.splice(
            0, self._model.get_n_items(), [PackageItem(package) for package in self._filtered]
        )
        count = len(self._filtered)
        total_size = sum(package.size_bytes for package in self._packages)
        self._summary.set_label(
            f"{count:,} matching · {len(self._packages):,} installed · {self.apt_manager.format_size(total_size)}"
        )
        if not count:
            self._empty.set_description(tr("Try a different package name or summary."))
            self._stack.set_visible_child_name("empty")
        else:
            self._stack.set_visible_child_name("list")

    def _bind_row(self, _factory, item) -> None:
        item.set_child(self._make_row(item.get_item().package))

    def _activate(self, _view, position: int) -> None:
        item = self._model.get_item(position)
        if self._data_ready and item is not None:
            self.window.show_package_details(item.package)

    def _make_row(self, package: PackageInfo) -> Gtk.Box:
        row = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        row.append(package_icon(package))
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        name = Gtk.Label(label=package.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        details.append(name)
        subtitle = " · ".join(
            part
            for part in (
                package.installed_version or "",
                self.apt_manager.format_size(package.size_bytes),
                package.section,
            )
            if part
        )
        version = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        version.add_css_class("caption")
        version.add_css_class("dim-label")
        details.append(version)
        details.set_tooltip_text(f"{package.name}\n{subtitle}")
        row.append(details)
        remove = Gtk.Button(label="Held" if package.is_held else "Remove", valign=Gtk.Align.CENTER)
        remove.add_css_class("destructive-action")
        remove.set_sensitive(not package.is_held)
        remove.set_tooltip_text(
            "This package is held by APT. Release its hold before removing it."
            if package.is_held
            else f"Review removal of {package.name}"
        )
        remove.connect("clicked", self._on_remove, package.full_name or package.name)
        row.append(remove)
        return row

    def _on_remove(self, _button: Gtk.Button, package_name: str) -> None:
        self.window.confirm_and_run(
            f"Remove {package_name}",
            "APT may remove packages that depend on this package. Review the operation details carefully.",
            self.apt_manager.helper_command("remove", package_name),
            destructive=True,
        )
