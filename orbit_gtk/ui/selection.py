"""An editable mixed-action basket; APT resolves it afresh when reviewed."""

from gi.repository import Adw, Gtk

from orbit_gtk.backend.transaction_options import option_arguments
from orbit_gtk.i18n import tr
from orbit_gtk.ui.widgets import action_row


class PackageSelection(Adw.Dialog):
    def __init__(self, window):
        super().__init__(
            title=tr("Selected package changes"), content_width=600, content_height=480
        )
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        group = Adw.PreferencesGroup(
            description=tr(
                "Selections are requests, not an installation plan. APT will resolve the current dependencies before you approve."
            )
        )
        page.add(group)
        for name, action in list(window._package_selection.items()):
            row = action_row(title=name, subtitle=action.capitalize())
            remove = Gtk.Button(label=tr("Discard"), valign=Gtk.Align.CENTER)

            def discard(_, key=name, item=row):
                window._package_selection.pop(key, None)
                window.save_selection()
                group.remove(item)

            remove.connect("clicked", discard)
            row.add_suffix(remove)
            group.add(row)
        review = Gtk.Button(label=tr("Resolve and review"), halign=Gtk.Align.CENTER)

        def apply(_):
            requests = [
                {"name": name, "action": action}
                for name, action in window._package_selection.items()
            ]
            if not requests:
                window.show_toast("Select packages first")
                return
            self.close()
            window.run_privileged(
                "Review selected packages",
                window.apt_manager.helper_command(
                    "batch", *option_arguments({"requests": requests})
                ),
            )

        review.connect("clicked", apply)
        group.add(review)
