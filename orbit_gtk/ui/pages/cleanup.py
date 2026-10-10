"""Cleanup page – shows reclaimable disk space with live sizes."""

import threading

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import CleanupItem
from orbit_gtk.i18n import tr
from orbit_gtk.ui.widgets import action_row


class CleanupPage(Gtk.Box):
    def __init__(self, apt_manager: AptManager, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.apt_manager = apt_manager
        self.window = window
        self._loaded = False
        self._generation = 0
        self._data_ready = False
        self._items: list[CleanupItem] = []
        self._checks: dict[str, Gtk.CheckButton] = {}

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.append(scroll)

        self._pref_page = Adw.PreferencesPage()
        scroll.set_child(self._pref_page)

        # ── Summary group ─────────────────────────────────────────── #
        self._summary_group = Adw.PreferencesGroup(title=tr("Space to Reclaim"))
        self._pref_page.add(self._summary_group)

        self._total_row = action_row(title=tr("Calculating…"), subtitle="")
        self._total_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        self._spinner_box = Gtk.Box()
        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._spinner_box.append(self._spinner)
        self._total_row.add_suffix(self._spinner_box)
        self._summary_group.add(self._total_row)
        self._retry = Gtk.Button(label=tr("Retry"), valign=Gtk.Align.CENTER, visible=False)
        self._retry.connect("clicked", lambda _: self.invalidate())
        self._total_row.add_suffix(self._retry)

        # ── Items group with a stable ListBox inside ──────────────── #
        # We use a plain Gtk.ListBox-style approach via ActionRows stored
        # in a separate group. We rebuild by removing individual rows.
        self._items_group = Adw.PreferencesGroup(title=tr("Items"))
        self._pref_page.add(self._items_group)
        # Keep a flat list of rows so we can remove them individually
        self._item_rows: list[Adw.ActionRow] = []

        dependencies = Adw.PreferencesGroup(title=tr("Unused dependencies"))
        dependency_row = action_row(
            title=tr("Review packages no longer needed"),
            subtitle=tr(
                "APT selects unused dependencies. Review every removal before applying."
            ),
        )
        review = Gtk.Button(label=tr("Review"), valign=Gtk.Align.CENTER)
        review.connect("clicked", self._on_autoremove)
        dependency_row.add_suffix(review)
        dependency_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dependency_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        dependency_list.append(dependency_row)
        dependency_controls.append(dependency_list)
        self._purge_configs = Gtk.CheckButton(
            label=tr("Also remove configurations"),
            margin_start=12,
            margin_end=12,
            margin_bottom=12,
        )
        self._purge_configs.set_tooltip_text(
            tr("Remove configuration files belonging to the unused packages selected by APT.")
        )
        dependency_controls.append(self._purge_configs)
        dependencies.add(dependency_controls)
        row = action_row(
            title=tr("Purge configurations of removed packages"),
            subtitle=tr("Remove configuration files left behind by packages already removed."),
        )
        button = Gtk.Button(label=tr("Review"), valign=Gtk.Align.CENTER)
        button.connect(
            "clicked",
            lambda _: self.window.run_privileged(
                "Review cleanup", self.apt_manager.helper_command("purge-config")
            ),
        )
        row.add_suffix(button)
        dependencies.add(row)
        self._pref_page.add(dependencies)

        # Clean button
        btn_box = Gtk.Box(spacing=12)
        btn_box.set_margin_start(12)
        btn_box.set_margin_end(12)
        btn_box.set_margin_bottom(16)
        self._clean_btn = Gtk.Button(label=tr("Clean Selected Items"), valign=Gtk.Align.CENTER)
        self._clean_btn.add_css_class("suggested-action")
        self._clean_btn.set_sensitive(False)
        self._clean_btn.connect("clicked", self._on_clean)
        btn_box.append(self._clean_btn)
        self.append(btn_box)

    def load_data(self):
        if self._loaded:
            return
        self._loaded = True
        self._data_ready = False
        self._generation += 1
        self._retry.set_visible(False)
        self._clean_btn.set_sensitive(False)
        self._total_row.set_title(tr("Calculating…"))
        self._total_row.set_subtitle("")
        self._items_group.set_sensitive(False)
        threading.Thread(target=self._fetch, args=(self._generation,), daemon=True).start()

    def invalidate(self):
        self._loaded = False
        self._clean_btn.set_sensitive(False)
        self._spinner_box.set_visible(True)
        self._spinner.start()
        self.load_data()
        return False

    def _fetch(self, generation):
        try:
            items = self.apt_manager.get_cleanup_items()
        except Exception as error:
            GLib.idle_add(self._fetch_failed, generation, str(error))
        else:
            GLib.idle_add(self._apply_current, generation, items)

    def _fetch_failed(self, generation, message):
        if generation != self._generation:
            return False
        self._spinner.stop()
        self._spinner_box.set_visible(False)
        self._total_row.set_title(tr("Could not calculate cleanup space"))
        self._total_row.set_subtitle(message)
        self._retry.set_visible(True)
        return False

    def _apply_current(self, generation, items):
        if generation == self._generation:
            self._apply(items)
        return False

    def _apply(self, items: list[CleanupItem]):
        self._data_ready = True
        self._items_group.set_sensitive(True)
        self._items = items
        self._checks.clear()

        # Remove old item rows individually (safe, no group re-add)
        for row in self._item_rows:
            self._items_group.remove(row)
        self._item_rows.clear()

        # Stop and hide spinner
        self._spinner.stop()
        self._spinner_box.set_visible(False)

        total = sum(i.size_bytes for i in items)
        self._total_row.set_title(
            self.apt_manager.format_size(total) + " reclaimable" if total else "Nothing to clean"
        )
        self._total_row.set_subtitle(
            f"{len(items)} categor{'ies' if len(items) != 1 else 'y'}" if items else ""
        )

        if not items:
            empty_row = action_row(
                title=tr("Nothing to clean"),
                subtitle=tr("Your system cache is already clean"),
            )
            self._items_group.add(empty_row)
            self._item_rows.append(empty_row)
            self._clean_btn.set_label(tr("Clean Selected Items"))
            self._clean_btn.set_sensitive(False)
            return False

        for item in items:
            check = Gtk.CheckButton()
            check.set_active(item.is_selected)
            check.connect("toggled", self._on_toggle, item.key)
            self._checks[item.key] = check

            row = action_row(
                title=item.title,
                subtitle=f"{self.apt_manager.format_size(item.size_bytes)}  •  {item.description}",
            )
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self._items_group.add(row)
            self._item_rows.append(row)

        self._update_clean_button()
        return False

    def _on_toggle(self, check: Gtk.CheckButton, key: str):
        for item in self._items:
            if item.key == key:
                item.is_selected = check.get_active()
        self._update_clean_button()

    def _update_clean_button(self):
        selected_size = sum(item.size_bytes for item in self._items if item.is_selected)
        self._clean_btn.set_label(f"Clean Selected ({self.apt_manager.format_size(selected_size)})")
        self._clean_btn.set_sensitive(self._data_ready and selected_size > 0)

    def _on_autoremove(self, _button):
        action = "autopurge" if self._purge_configs.get_active() else "autoremove"
        self.window.run_privileged(
            "Review unused dependencies", self.apt_manager.helper_command(action)
        )

    def _on_clean(self, _btn):
        if not self._data_ready:
            return
        selected_keys = [i.key for i in self._items if i.is_selected]
        if not selected_keys:
            return
        selected_lists = "apt_lists" in selected_keys
        self.window.confirm_and_run(
            "System Cleanup",
            (
                "This removes cached package archives and repository lists. Refresh package lists before browsing packages again."
                if selected_lists
                else "This removes downloaded package archives. Installed packages will not be removed."
            ),
            self.apt_manager.helper_command("clean", *selected_keys),
            destructive=True,
            on_done=lambda ok: (self.window.show_toast("Cleanup completed"),) if ok else None,
        )
