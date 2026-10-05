"""Package metadata presented without opening a terminal."""

from urllib.parse import urlparse

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.apt_manager import AptManager
from orbit_gtk.backend.models import PackageInfo
from orbit_gtk.ui.screenshots import ScreenshotGallery
from orbit_gtk.ui.widgets import action_row, package_icon


class PackageDialog(Adw.Dialog):
    def __init__(self, package: PackageInfo, *, on_action=None):
        super().__init__(title=package.name, content_width=580, content_height=540)
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        summary = Adw.PreferencesGroup(
            title=package.name, description=GLib.markup_escape_text(package.summary)
        )
        summary.set_header_suffix(package_icon(package, 64))
        page.add(summary)
        if on_action:
            actions = Adw.PreferencesGroup(title="Package actions")
            if package.is_held:
                actions.add(
                    action_row(
                        title="Held by APT",
                        subtitle="Release the APT hold before changing this package.",
                    )
                )
            else:
                choices = []
                if not package.is_installed or package.is_upgradable:
                    choices.append(
                        (
                            "install",
                            "Review upgrade" if package.is_installed else "Install",
                            "Review package and dependency changes before applying.",
                        )
                    )
                if package.is_installed:
                    choices.append(
                        (
                            "reinstall",
                            "Reinstall current version",
                            "Download the same version again to restore package files. Local configuration is kept.",
                        )
                    )
                    choices.append(
                        (
                            "remove",
                            "Remove",
                            "Review removal before applying. Personal files are kept.",
                        )
                    )
                    self._purge_configs = Gtk.CheckButton(label="Purge configuration files")
                    self._purge_configs.set_tooltip_text(
                        "Also remove package-managed system configuration files."
                    )
                for action, title, subtitle in choices:
                    row = action_row(title=title, subtitle=subtitle)
                    button = Gtk.Button(
                        label="Remove" if action == "remove" else "Review", valign=Gtk.Align.CENTER
                    )
                    button.add_css_class(
                        "destructive-action"
                        if action in {"remove", "purge"}
                        else "suggested-action"
                    )

                    def activate(_button, selected=action):
                        self.close()
                        on_action(
                            "purge"
                            if selected == "remove" and self._purge_configs.get_active()
                            else selected
                        )

                    button.connect("clicked", activate)
                    if action == "remove":
                        row.add_suffix(self._purge_configs)
                    row.add_suffix(button)
                    actions.add(row)
            page.add(actions)
        fields = (
            ("Installed version", package.installed_version or "Not installed"),
            ("Available version", package.latest_version or "Unavailable"),
            ("Repository", package.source),
            (
                "Installed size" if package.is_installed else "Size after installation",
                AptManager.format_size(package.size_bytes),
            ),
            ("Download size", AptManager.format_size(package.download_size_bytes)),
            ("Section", package.section or "Unknown"),
            ("Installation reason", "Manual" if package.is_manual else "Dependency"),
        )
        for title, value in fields:
            if title == "Download size" and package.is_installed and not package.is_upgradable:
                continue
            if title == "Installation reason" and not package.is_installed:
                continue
            summary.add(action_row(title=title, subtitle=value, subtitle_selectable=True))
        description = Adw.PreferencesGroup(title="Description")
        description.add(
            Gtk.Label(
                label=package.description or package.summary, wrap=True, xalign=0, selectable=True
            )
        )
        page.add(description)
        if package.screenshots:
            page.add(ScreenshotGallery(package.screenshots))
        if urlparse(package.homepage).scheme in {"http", "https"}:
            description.add(
                Gtk.LinkButton(
                    uri=package.homepage, label="Project website", halign=Gtk.Align.START
                )
            )
