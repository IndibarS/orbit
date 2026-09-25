"""Updates page with one consistent, confirmed transaction flow."""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import PackageInfo
from orbit_gtk.ui.operation_view import OperationView
from orbit_gtk.ui.widgets import action_row, package_icon


class UpdatesPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._loaded = False
        self._generation = 0
        self._operation: OperationView | None = None
        self._row_widgets: dict[str, dict] = {}
        self._pending: set[str] = set()
        self._pulsing: set[str] = set()
        self._running = False

        self._banner = Adw.Banner(button_label="Upgrade all", revealed=False)
        self._banner.connect("button-clicked", self._on_upgrade_all)
        self.append(self._banner)
        self._upgrade_options = Gtk.MenuButton(
            label="Upgrade options",
            halign=Gtk.Align.END,
            margin_end=12,
            margin_top=6,
            visible=False,
        )
        self._upgrade_popover = Gtk.Popover()
        options = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        options.append(
            Gtk.Label(
                label="Full upgrades may install or remove dependencies.\nReview every change before applying.\nYour configured release and sources stay the same.",
                wrap=True,
                max_width_chars=40,
                xalign=0,
            )
        )
        full_upgrade = Gtk.Button(label="Review full upgrade")
        full_upgrade.connect("clicked", self._on_full_upgrade)
        options.append(full_upgrade)
        self._upgrade_popover.set_child(options)
        self._upgrade_options.set_popover(self._upgrade_popover)
        self.append(self._upgrade_options)
        self._operation_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        operation_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            max_content_height=360,
        )
        operation_scroll.set_child(self._operation_slot)
        self.append(operation_scroll)

        self._stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.append(self._stack)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        spinner = Gtk.Spinner(halign=Gtk.Align.CENTER)
        spinner.start()
        loading.append(spinner)
        loading_label = Gtk.Label(label="Loading updates…")
        loading_label.add_css_class("dim-label")
        loading.append(loading_label)
        self._stack.add_named(loading, "loading")
        self._empty = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="No cached upgrades")
        check = Gtk.Button(
            label="Refresh package lists", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER
        )
        check.add_css_class("suggested-action")
        check.connect("clicked", self._on_refresh)
        self._empty.set_description(
            "No upgrades are available in the cached package lists. Refresh to check repositories."
        )
        self._empty.set_child(check)
        self._stack.add_named(self._empty, "empty")
        self._error = Adw.StatusPage(
            icon_name="dialog-error-symbolic", title="Could not load updates"
        )
        retry = Gtk.Button(label="Retry loading", halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _: self.window.refresh_all())
        self._error.set_child(retry)
        self._stack.add_named(self._error, "error")
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_top(12)
        self._list.set_margin_bottom(12)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        scroll.set_child(self._list)
        self._stack.add_named(scroll, "list")

    def load_data(self) -> None:
        if self._loaded or self._operation is not None:
            return
        self._loaded = True
        self._generation += 1
        generation = self._generation
        self._stack.set_visible_child_name("loading")
        threading.Thread(
            target=self._fetch, args=(generation,), name="orbit-updates", daemon=True
        ).start()

    def invalidate(self) -> bool:
        self._loaded = False
        self.load_data()
        return False

    def _fetch(self, generation: int) -> None:
        try:
            packages = self.apt_manager.get_upgradable_packages()
            error = self.apt_manager.last_error
        except Exception as failure:
            packages, error = [], str(failure) or type(failure).__name__
        GLib.idle_add(self._apply, generation, packages, error)

    def _apply(self, generation: int, packages: list[PackageInfo], error: str | None) -> bool:
        if generation != self._generation or self._operation is not None:
            return False
        while child := self._list.get_first_child():
            self._list.remove(child)
        self._row_widgets.clear()
        self._upgrade_options.set_visible(bool(packages) and not error)
        if error:
            self._error.set_description(error)
            self._banner.set_revealed(False)
            self._stack.set_visible_child_name("error")
            return False
        if not packages:
            self._banner.set_revealed(False)
            self._stack.set_visible_child_name("empty")
            return False
        held = sum(package.is_held_back for package in packages)
        title = f"{len(packages)} update{'s' if len(packages) != 1 else ''} available"
        if held:
            title += f" · {held} kept back"
        self._banner.set_button_label(
            "Upgrade all" if held < len(packages) else "Check upgrade plan"
        )
        self._banner.set_title(title)
        self._banner.set_revealed(True)
        for package in packages:
            self._list.append(self._make_row(package))
        self._stack.set_visible_child_name("list")
        return False

    def _make_row(self, package: PackageInfo) -> Adw.ActionRow:
        versions = f"{package.installed_version or '—'} → {package.latest_version or '—'}"
        detail = (
            package.held_reason
            if package.is_held_back
            else self.apt_manager.format_size(package.download_size_bytes)
        )
        row = action_row(
            title=package.name,
            subtitle=f"{versions} · {detail}" if detail else versions,
        )
        row.set_activatable(True)
        row.connect("activated", lambda _: self.window.show_package_details(package))
        row.add_prefix(package_icon(package))
        key = package.full_name or package.name
        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER
        )
        progress_box.set_size_request(170, -1)
        progress_box.set_visible(False)
        status = Gtk.Label(
            label="Kept back" if package.is_held_back else "Ready",
            xalign=0,
            wrap=True,
            max_width_chars=28,
        )
        status.add_css_class("caption")
        progress_box.append(status)
        bar = Gtk.ProgressBar()
        bar.set_tooltip_text(
            "Download progress is measured; installation pulses until APT confirms completion."
        )
        progress_box.append(bar)
        row.add_suffix(progress_box)
        self._row_widgets[key] = {
            "bar": bar,
            "status": status,
            "button": None,
            "row": row,
            "progress_box": progress_box,
        }
        badge = Gtk.Label(label="Kept back", visible=package.is_held_back)
        badge.add_css_class("badge")
        badge.add_css_class("warning")
        badge.set_tooltip_text(package.held_reason)
        row.add_suffix(badge)
        self._row_widgets[key]["badge"] = badge
        if not package.is_held:
            button = Gtk.Button(
                label="Review upgrade" if package.is_held_back else "Upgrade",
                valign=Gtk.Align.CENTER,
            )
            button.add_css_class("flat")
            button.connect("clicked", self._on_upgrade_one, key)
            row.add_suffix(button)
            self._row_widgets[key]["button"] = button
        return row

    def _on_refresh(self, _button: Gtk.Button) -> None:
        self.start_operation("Refresh package lists", self.apt_manager.helper_command("update"))

    def _on_upgrade_one(self, _button: Gtk.Button, package_name: str) -> None:
        self.start_operation(
            f"Upgrade {package_name}", self.apt_manager.helper_command("install", package_name)
        )

    def _on_upgrade_all(self, _banner: Adw.Banner) -> None:
        self.start_operation("Upgrade packages", self.apt_manager.helper_command("upgrade"))

    def _on_full_upgrade(self, _button) -> None:
        self._upgrade_popover.popdown()
        self.start_operation("Review full upgrade", self.apt_manager.helper_command("full-upgrade"))

    def start_operation(self, title: str, command: list[str]) -> None:
        if not self.window.claim_operation():
            return
        self._generation += 1  # An in-flight page load must not erase live rows.
        self._running = True
        self._pending.clear()
        self._pulsing.clear()
        self._banner.set_revealed(False)
        self._upgrade_options.set_visible(False)
        self._stack.set_visible_child_name("list")
        for widgets in self._row_widgets.values():
            if widgets["button"]:
                widgets["button"].set_sensitive(False)
        self._operation = OperationView(
            title,
            command,
            compact=True,
            on_event=self._operation_event,
            on_done=self._operation_done,
            on_close=self._dismiss_operation,
        )
        self._operation_slot.append(self._operation)
        GLib.timeout_add(100, self._pulse_rows)

    def _operation_event(self, event: dict) -> None:
        kind = event["event"]
        if kind == "plan":
            for change in event["changes"]:
                key = change["name"]
                if key not in self._row_widgets:
                    package = PackageInfo(
                        name=key,
                        full_name=key,
                        installed_version=change["old_version"],
                        latest_version=change["new_version"],
                    )
                    self._list.append(self._make_row(package))
                widgets = self._row_widgets[key]
                # A newly reviewed plan may include a package that the earlier
                # normal-upgrade snapshot kept back. Do not show both states.
                widgets["badge"].set_visible(False)
                if widgets["button"]:
                    widgets["button"].set_sensitive(False)
                widgets["status"].set_label(f"Queued for {change['action']}")
                widgets["bar"].set_fraction(0)
                self._pending.add(key)
            self._mark_kept_back(event.get("kept_back", []))
        elif kind == "kept-back":
            self._mark_kept_back(event.get("packages", []))
        elif kind in {"package-progress", "progress"} and event.get("package"):
            key = event["package"]
            if key not in self._row_widgets:
                return
            widgets = self._row_widgets[key]
            widgets["progress_box"].set_visible(True)
            widgets["status"].set_label(event.get("message", "Working…"))
            widgets["status"].add_css_class("accent")
            # APT's install percentage is transaction-wide, not per-package.
            # Only acquisition byte counts provide a real per-package fraction.
            percent = event.get("percent") if kind == "package-progress" else None
            if percent is None:
                self._pulsing.add(key)
                widgets["bar"].pulse()
            else:
                self._pulsing.discard(key)
                widgets["bar"].set_fraction(max(0, min(1, percent / 100)))

    def _mark_kept_back(self, names: list[str]) -> None:
        for key in names:
            widgets = self._row_widgets.get(key)
            if widgets is None:
                # Upgrade can be started from Home before this page has loaded.
                self._list.append(
                    self._make_row(
                        PackageInfo(
                            name=key,
                            full_name=key,
                            is_held_back=True,
                            held_reason="Excluded by APT from the reviewed upgrade",
                        )
                    )
                )
                widgets = self._row_widgets[key]
            widgets["badge"].set_visible(True)
            widgets["badge"].set_tooltip_text(
                "APT excluded this package from the reviewed upgrade."
            )
            widgets["progress_box"].set_visible(False)
            if widgets["button"]:
                widgets["button"].set_visible(False)
            self._pending.discard(key)
            self._pulsing.discard(key)

    def _pulse_rows(self) -> bool:
        if not self._running:
            return False
        for key in self._pulsing:
            self._row_widgets[key]["bar"].pulse()
        return True

    def _operation_done(self, success: bool) -> None:
        self._running = False
        self._pulsing.clear()
        cancelled = self._operation._cancelled
        for key in self._pending:
            widgets = self._row_widgets[key]
            widgets["progress_box"].set_visible(not cancelled)
            widgets["bar"].set_fraction(1 if success else 0)
            widgets["status"].remove_css_class("accent")
            widgets["status"].add_css_class(
                "success" if success else "warning" if cancelled else "error"
            )
            widgets["status"].set_label(
                "Completed" if success else "Cancelled" if cancelled else "Stopped — check details"
            )
        # Keep completed rows visible until Done; other pages get fresh state.
        self.window.refresh_all()

    def _dismiss_operation(self) -> None:
        if self._running:
            return
        self._operation_slot.remove(self._operation)
        self._operation = None
        self.window.release_operation()
        self.invalidate()
