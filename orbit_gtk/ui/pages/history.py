"""Transaction history page for the Nala and APT histories parsed by the backend."""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk, Pango

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import HistoryTransaction, PackageInfo
from orbit_gtk.i18n import tr
from orbit_gtk.ui.widgets import action_row, expander_row, status_chip

_ACTIONS = {
    "install": ("Installed", "list-add-symbolic", "success", "installed_pkgs"),
    "upgrade": ("Upgraded", "software-update-available-symbolic", "accent", "upgraded_pkgs"),
    "remove": ("Removed", "list-remove-symbolic", "warning", "removed_pkgs"),
    "purge": ("Purged", "user-trash-symbolic", "error", "purged_pkgs"),
    "reinstall": ("Reinstalled", "view-refresh-symbolic", "accent", "reinstalled_pkgs"),
    "downgrade": ("Downgraded", "go-down-symbolic", "warning", "downgraded_pkgs"),
}


class HistoryPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, _window: object) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = _window
        self._limit = 200
        self._loaded = False
        self._generation = 0
        self._rows: list[Gtk.Widget] = []

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.connect("edge-reached", self._edge_reached)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scroll)
        page = Adw.PreferencesPage()
        scroll.set_child(page)
        self._group = Adw.PreferencesGroup(
            title=tr("Transactions"), description=tr("Changes recorded by Orbit, Nala and APT")
        )
        page.add(self._group)

    def _edge_reached(self, _scroll, position):
        if position == Gtk.PositionType.BOTTOM and len(self._rows) >= self._limit:
            self._limit += 200
            self.invalidate()

    def load_data(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._generation += 1
        generation = self._generation
        self._show_loading()
        threading.Thread(
            target=self._fetch, args=(generation, self._limit), name="orbit-history", daemon=True
        ).start()

    def invalidate(self) -> bool:
        self._loaded = False
        self.load_data()
        return False

    def _show_loading(self) -> None:
        self._clear_rows()
        row = action_row(title=tr("Loading transaction history…"))
        spinner = Gtk.Spinner()
        spinner.start()
        row.add_prefix(spinner)
        self._group.add(row)
        self._rows.append(row)

    def _fetch(self, generation: int, limit: int) -> None:
        try:
            transactions = self.apt_manager.get_history(limit)
        except Exception as error:
            GLib.idle_add(self._show_error, generation, str(error) or type(error).__name__)
        else:
            GLib.idle_add(self._apply, generation, transactions)

    def _show_error(self, generation: int, message: str) -> bool:
        if generation != self._generation:
            return False
        self._clear_rows()
        row = action_row(title=tr("History unavailable"), subtitle=message)
        retry = Gtk.Button(label=tr("Retry"), valign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _: self.invalidate())
        row.add_suffix(retry)
        self._group.add(row)
        self._rows.append(row)
        return False

    def _apply(self, generation: int, transactions: list[HistoryTransaction]) -> bool:
        if generation != self._generation:
            return False
        self._clear_rows()
        if not transactions:
            row = action_row(
                title=tr("No transaction history found"),
                subtitle=tr("Orbit, Nala and APT history logs are empty or unavailable."),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
            self._group.add(row)
            self._rows.append(row)
            return False
        for transaction in transactions:
            row = self._make_row(transaction)
            self._group.add(row)
            self._rows.append(row)
        return False

    def _replay(self, transaction, undo):
        from orbit_gtk.backend.replay import replay_requests
        from orbit_gtk.backend.transaction_options import option_arguments

        try:
            requests = replay_requests(transaction, undo)
            self.window.run_privileged(
                "Review history replay",
                self.apt_manager.helper_command("batch", *option_arguments({"requests": requests})),
            )
        except ValueError as error:
            self.window.show_toast(str(error))

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._group.remove(row)
        self._rows.clear()

    def _make_row(self, transaction: HistoryTransaction) -> Adw.ExpanderRow:
        row = expander_row(
            title=f"{transaction.operation.capitalize()} · {transaction.date}",
            subtitle=(
                f"{transaction.altered_count} package{'s' if transaction.altered_count != 1 else ''}"
                if transaction.status in {"Completed", "Recorded"}
                else f"{transaction.altered_count} requested package changes"
            ),
        )
        row.add_css_class("history-transaction")
        _, icon_name, color, _ = _ACTIONS.get(
            transaction.operation,
            (
                "",
                "view-refresh-symbolic"
                if transaction.operation == "update"
                else "document-open-recent-symbolic",
                "accent" if transaction.operation == "update" else "dim-label",
                "",
            ),
        )
        icon = Gtk.Image(icon_name=icon_name, pixel_size=24)
        icon.add_css_class(color)
        row.add_prefix(icon)
        status_color = (
            "error"
            if transaction.status == "Failed"
            else "success"
            if transaction.status == "Completed"
            else "warning"
        )
        row.add_suffix(status_chip(transaction.status, status_color))
        populated = False

        def expanded(expander, _property):
            nonlocal populated
            if populated or not expander.get_expanded():
                return
            populated = True
            summary = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE,
                homogeneous=False,
                max_children_per_line=6,
                row_spacing=6,
                column_spacing=6,
                margin_start=12,
                margin_end=12,
                margin_top=10,
                margin_bottom=10,
            )
            groups = [
                (key, *values, getattr(transaction, values[3]))
                for key, values in _ACTIONS.items()
                if getattr(transaction, values[3])
            ]
            for key, label, _icon, color, _field, packages in groups:
                text = (
                    label.lower()
                    if transaction.status in {"Completed", "Recorded"}
                    else f"{key} requests"
                )
                summary.insert(status_chip(f"{len(packages)} {text}", color), -1)
            if groups:
                row.add_row(summary)
            else:
                row.add_row(action_row(title=tr("No package-level changes recorded")))
            for index, (key, label, icon_name, color, _field, packages) in enumerate(groups):
                group = expander_row(
                    title=label
                    if transaction.status in {"Completed", "Recorded"}
                    else f"{key.capitalize()} requests",
                    subtitle=f"{len(packages)} package{'s' if len(packages) != 1 else ''}",
                )
                group.add_css_class("history-branch")
                icon = Gtk.Image(icon_name=icon_name)
                icon.add_css_class(color)
                group.add_prefix(icon)
                self._add_package_list(group, key, packages)
                row.add_row(group)
                group.set_expanded(index == 0)
            details = expander_row(
                title=tr("Transaction details"), subtitle=tr("Command and requested user")
            )
            details.add_css_class("history-branch")
            details.add_row(
                action_row(
                    title=tr("Command"), subtitle=transaction.command, subtitle_selectable=True
                )
            )
            details.add_row(
                action_row(
                    title=tr("Requested by"),
                    subtitle=transaction.requested_by,
                    subtitle_selectable=True,
                )
            )
            row.add_row(details)
            if transaction.status == "Completed" and transaction.altered_count:
                replay = action_row(
                    title=tr("Review historical package changes"),
                    subtitle=tr(
                        "Undo restores package versions where available; it cannot restore deleted configuration or personal data."
                    ),
                )
                for undo in (True, False):
                    description = tr(
                        "Undo: review restoring the previous package versions. Deleted configuration cannot be restored."
                        if undo
                        else "Redo: review applying these package changes again."
                    )
                    button = Gtk.Button(
                        icon_name="edit-undo-symbolic" if undo else "edit-redo-symbolic",
                        valign=Gtk.Align.CENTER,
                    )
                    button.set_tooltip_text(description)
                    button.update_property([Gtk.AccessibleProperty.LABEL], [description])
                    button.connect(
                        "clicked", lambda _, reverse=undo: self._replay(transaction, reverse)
                    )
                    replay.add_suffix(button)
                row.add_row(replay)

        row.connect("notify::expanded", expanded)
        return row

    @staticmethod
    def _add_package_list(group, action, packages):
        """Virtualize long transactions; construct widgets only for visible packages."""
        populated = False

        def expanded(expander, _property):
            nonlocal populated
            if populated or not expander.get_expanded():
                return
            populated = True
            factory = Gtk.SignalListItemFactory()

            def setup(_factory, item):
                box = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL,
                    spacing=3,
                    margin_start=16,
                    margin_end=16,
                    margin_top=8,
                    margin_bottom=8,
                )
                name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
                version = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
                version.add_css_class("caption")
                version.add_css_class("dim-label")
                box.append(name)
                box.append(version)
                item.set_child(box)

            def bind(_factory, item):
                package = packages[item.get_position()]
                box = item.get_child()
                name = box.get_first_child()
                name.set_label(package.name)
                detail = HistoryPage._package_subtitle(action, package)
                name.get_next_sibling().set_label(detail)
                box.set_tooltip_text(f"{package.name}\n{detail}")

            factory.connect("setup", setup)
            factory.connect("bind", bind)
            model = Gtk.StringList.new([package.name for package in packages])
            view = Gtk.ListView(model=Gtk.NoSelection(model=model), factory=factory)
            view.add_css_class("boxed-list")
            scroll = Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                min_content_height=min(len(packages), 5) * 56,
                max_content_height=280,
            )
            scroll.set_child(view)
            group.add_row(scroll)

        group.connect("notify::expanded", expanded)

    @staticmethod
    def _package_subtitle(action: str, package: PackageInfo) -> str:
        if action in {"upgrade", "downgrade"}:
            return f"{package.installed_version or '—'} → {package.latest_version or '—'}"
        version = package.latest_version or package.installed_version or "Unknown version"
        return f"Version {version}"
