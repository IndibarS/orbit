"""Advanced options are collected before APT generates its authoritative review."""

from gi.repository import Adw, Gtk

from orbit_gtk.backend.transaction_options import option_arguments
from orbit_gtk.i18n import tr


class TransactionOptions(Adw.Dialog):
    def __init__(self, window, action="install", names=()):
        super().__init__(title=tr("Plan package changes"), content_width=620, content_height=650)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        group = Adw.PreferencesGroup(
            description=tr(
                "These choices affect the next operation only. Review APT’s complete plan before applying."
            )
        )
        page.add(group)
        actions = [
            "install",
            "remove",
            "purge",
            "reinstall",
            "upgrade",
            "full-upgrade",
            "autoremove",
            "autopurge",
            "purge-config",
            "fix-broken",
        ]
        selected = Adw.ComboRow(
            title=tr("Operation"),
            model=Gtk.StringList.new([a.replace("-", " ").capitalize() for a in actions]),
        )
        selected.set_selected(actions.index(action))
        group.add(selected)
        packages = Adw.EntryRow(title=tr("Packages · space-separated; name=version supported"))
        packages.set_text(" ".join(names))
        group.add(packages)
        excludes = Adw.EntryRow(title=tr("Exclude from upgrade · space-separated patterns"))
        group.add(excludes)
        release = Adw.EntryRow(title=tr("Target release · optional configured suite"))
        group.add(release)
        checks = {}
        for key, title in (
            ("download_only", "Download only"),
            ("refresh", "Refresh package lists first"),
            ("interactive", "Show package configuration questions"),
            ("purge", "Purge configurations of removed packages"),
            ("autoremove", "Also remove unused dependencies"),
        ):
            row = Adw.SwitchRow(title=title)
            group.add(row)
            checks[key] = row
        policy = {}
        for key in ("recommends", "suggests"):
            row = Adw.ComboRow(
                title=f"Install {key}", model=Gtk.StringList.new(["Use APT settings", "Yes", "No"])
            )
            group.add(row)
            policy[key] = row
        conffile = Adw.ComboRow(
            title=tr("Modified system configuration files"),
            subtitle=tr(
                "Services may restart after installation. Enable questions above for interactive package setup."
            ),
            model=Gtk.StringList.new(
                ["Keep local files", "Replace with package versions", "Ask and show differences"]
            ),
        )
        group.add(conffile)
        error = Gtk.Label(wrap=True)
        error.add_css_class("error")
        footer = Adw.PreferencesGroup()
        footer.add(error)
        page.add(footer)
        review = Gtk.Button(label=tr("Resolve and review"), halign=Gtk.Align.CENTER)
        review.add_css_class("suggested-action")
        footer.add(review)

        def changed(*_):
            operation = actions[selected.get_selected()]
            packages.set_visible(operation in {"install", "remove", "purge", "reinstall"})
            excludes.set_visible(operation in {"upgrade", "full-upgrade"})

        selected.connect("notify::selected", changed)
        changed()

        def apply(_):
            operation = actions[selected.get_selected()]
            specs = packages.get_text().split() if packages.get_visible() else []
            versions, names = {}, []
            for spec in specs:
                name, separator, version = spec.partition("=")
                names.append(name)
                if separator:
                    versions[name] = version
            if packages.get_visible() and not names:
                error.set_label(tr("Enter at least one package."))
                return
            options = {k: row.get_active() for k, row in checks.items()}
            if versions:
                options["versions"] = versions
            if excludes.get_visible() and excludes.get_text().strip():
                options["exclude"] = excludes.get_text().split()
            if release.get_text().strip():
                options["target_release"] = release.get_text().strip()
            for key, row in policy.items():
                if row.get_selected():
                    options[key] = row.get_selected() == 1
            options["conffile"] = ("keep", "replace", "ask")[conffile.get_selected()]
            try:
                command = window.apt_manager.helper_command(
                    operation, *names, *option_arguments(options)
                )
            except ValueError as failure:
                error.set_label(str(failure))
                return
            self.close()
            window.run_privileged("Review package changes", command)

        review.connect("clicked", apply)
