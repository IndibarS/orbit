"""Home / Dashboard page."""

import threading

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import PackageHealth
from orbit_gtk.ui.widgets import action_row


class HomePage(Gtk.ScrolledWindow):
    def __init__(self, apt_manager: AptManager, window):
        super().__init__()
        self.apt_manager = apt_manager
        self.window = window
        self._loaded = False
        self._generation = 0
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._page = Adw.PreferencesPage()
        self.set_child(self._page)
        self._recovery_group = Adw.PreferencesGroup(
            title="Package state needs attention", visible=False
        )
        self._recovery = action_row(title="Checking package state…")
        icon = Gtk.Image(icon_name="dialog-warning-symbolic")
        icon.add_css_class("warning")
        self._recovery.add_prefix(icon)
        self._repair_button = Gtk.Button(label="Review repair", valign=Gtk.Align.CENTER)
        self._repair_button.connect("clicked", self._on_repair)
        self._recovery.add_suffix(self._repair_button)
        self._recovery_group.add(self._recovery)
        self._page.add(self._recovery_group)

        # ── System summary group ──────────────────────────────────── #
        self._sys_group = Adw.PreferencesGroup(title="System")
        self._page.add(self._sys_group)

        self._row_distro = self._make_row("Distribution", "—", "computer-symbolic")
        self._row_kernel = self._make_row("Kernel", "—", "application-x-firmware-symbolic")
        self._row_arch = self._make_row("Architecture", "—", "system-run-symbolic")
        for r in (self._row_distro, self._row_kernel, self._row_arch):
            self._sys_group.add(r)

        # ── Package stats group ───────────────────────────────────── #
        self._pkg_group = Adw.PreferencesGroup(title="Packages")
        self._retry = Gtk.Button(label="Retry", valign=Gtk.Align.CENTER, visible=False)
        self._retry.connect("clicked", lambda _: self.window.refresh_all())
        self._pkg_group.set_header_suffix(self._retry)
        self._page.add(self._pkg_group)

        self._row_installed = self._make_row("Installed", "—", "emblem-ok-symbolic")
        self._row_upgradable = self._make_row(
            "Upgradable", "—", "software-update-available-symbolic"
        )
        self._row_cache = self._make_row("Cache Size", "—", "drive-harddisk-symbolic")
        for r in (self._row_installed, self._row_upgradable, self._row_cache):
            self._pkg_group.add(r)

        # ── Quick actions ─────────────────────────────────────────── #
        act_group = Adw.PreferencesGroup(title="Quick Actions")
        self._page.add(act_group)

        upgrade_row = action_row(
            title="Upgrade All Packages",
            subtitle="Apply all pending system upgrades",
        )
        upgrade_row.set_activatable(True)
        upgrade_row.add_prefix(Gtk.Image.new_from_icon_name("software-update-available-symbolic"))
        upgrade_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        upgrade_row.connect("activated", self._on_upgrade_all)
        act_group.add(upgrade_row)

        cleanup_row = action_row(
            title="Clean Package Cache",
            subtitle="Remove downloaded .deb files from cache",
        )
        cleanup_row.set_activatable(True)
        cleanup_row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        cleanup_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        cleanup_row.connect("activated", self._on_clean_cache)
        act_group.add(cleanup_row)

        repair = action_row(
            title="Repair interrupted installation",
            subtitle="Finish configuring packages after an interrupted transaction",
        )
        repair.set_activatable(True)
        repair.add_prefix(Gtk.Image(icon_name="applications-system-symbolic"))
        repair.connect("activated", self._on_repair)
        act_group.add(repair)

    def _on_repair(self, _row):
        self.window.confirm_and_run(
            "Repair interrupted installation",
            "Configure pending packages while keeping local configuration files.",
            self.apt_manager.helper_command("repair"),
        )

    # ── Helpers ───────────────────────────────────────────────────── #
    def _make_row(self, title, subtitle, icon):
        row = action_row(title=title, subtitle=subtitle)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        return row

    # ── Data loading ──────────────────────────────────────────────── #
    def load_data(self):
        if self._loaded:
            return
        self._loaded = True
        self._retry.set_visible(False)
        for row in (self._row_installed, self._row_upgradable, self._row_cache):
            row.set_subtitle("Loading…")
            row.remove_css_class("error")
        self._generation += 1
        threading.Thread(
            target=self._fetch, args=(self._generation,), name="orbit-home", daemon=True
        ).start()

    def invalidate(self):
        self._loaded = False
        self.load_data()
        return False

    def _fetch(self, generation):
        def read(operation, fallback, apt_error=False):
            try:
                value = operation()
                return value, self.apt_manager.last_error if apt_error else None
            except Exception as error:
                return fallback, str(error) or type(error).__name__

        info, system_error = read(self.apt_manager.get_system_info, {})
        installed, installed_error = read(self.apt_manager.get_installed_packages, [], True)
        upgradable, upgrade_error = read(self.apt_manager.get_upgradable_packages, [], True)
        cleanup, cleanup_error = read(self.apt_manager.get_cleanup_items, [])
        health, health_error = read(self.apt_manager.get_package_health, PackageHealth())
        cache_bytes = sum(item.size_bytes for item in cleanup if item.key == "apt_cache")
        GLib.idle_add(
            self._apply,
            generation,
            info,
            len(installed),
            len(upgradable),
            cache_bytes,
            {
                "system": system_error,
                "installed": installed_error,
                "upgradable": upgrade_error,
                "cache": cleanup_error,
                "health": health_error,
            },
            health,
        )

    def _apply(self, generation, info, installed, upgradable, cache_bytes, errors, health):
        if generation != self._generation:
            return False
        self._row_distro.set_subtitle(errors["system"] or info.get("distro", "—"))
        self._row_kernel.set_subtitle(info.get("kernel", "—"))
        self._row_arch.set_subtitle(info.get("arch", "—"))
        values = (
            (
                self._row_installed,
                "installed",
                f"{installed:,} package{'s' if installed != 1 else ''}",
            ),
            (
                self._row_upgradable,
                "upgradable",
                f"{upgradable} cached upgrade{'s' if upgradable != 1 else ''}"
                if upgradable
                else "No cached upgrades",
            ),
            (self._row_cache, "cache", self.apt_manager.format_size(cache_bytes)),
        )
        for row, key, value in values:
            row.set_subtitle(f"Unavailable: {errors[key]}" if errors[key] else value)
            row.remove_css_class("error")
            if errors[key]:
                row.add_css_class("error")
        self._row_upgradable.set_tooltip_text(
            "Refresh package lists to check repositories for new updates."
        )
        self._retry.set_visible(any(errors.values()))
        self._recovery_group.set_visible(health.needs_attention or bool(errors.get("health")))
        if errors.get("health"):
            self._recovery.set_title("Could not check package state")
            self._recovery.set_subtitle(errors["health"])
            self._repair_button.set_visible(False)
        elif health.needs_attention:
            self._recovery.set_title(
                f"{len(health.pending_configuration)} package{'s' if len(health.pending_configuration) != 1 else ''} "
                f"{'need' if len(health.pending_configuration) != 1 else 'needs'} configuration"
                if health.pending_configuration
                else "Some packages need repair"
            )
            names = (*health.pending_configuration, *health.reinstall_required)
            details = ", ".join(names[:5]) + (
                f" and {len(names) - 5} more" if len(names) > 5 else ""
            )
            if health.reinstall_required:
                details += "\nSome packages require reinstallation; configuration alone may not repair them."
            if health.broken_dependencies:
                details += f"\n{health.broken_dependencies} packages have broken dependencies."
            self._recovery.set_subtitle(details.strip())
            self._repair_button.set_visible(bool(health.pending_configuration))
        return False

    def _on_upgrade_all(self, _row):
        self.window.show_upgrades()

    def _on_clean_cache(self, _row):
        self.window.confirm_and_run(
            "Clean Package Cache",
            "This removes downloaded package archives. Installed packages will not be removed.",
            self.apt_manager.helper_command("clean"),
            destructive=True,
        )
