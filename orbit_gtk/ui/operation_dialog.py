"""Dialog host for operations outside the inline Updates page."""

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.ui.operation_view import OperationView


class OperationDialog(Adw.Dialog):
    def __init__(self, title, command, *, on_done=None):
        super().__init__(title=title, content_width=620, content_height=540)
        self.set_can_close(False)
        toolbar = Adw.ToolbarView()
        self.set_child(toolbar)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        toolbar.set_content(scroll)

        def completed(success):
            self.set_can_close(True)
            if on_done:
                on_done(success)
            if self.view.dismiss_automatically:
                host = self.get_root()
                if hasattr(host, "show_toast"):
                    host.show_toast(
                        "Operation cancelled" if self.view._cancelled else "Operation completed"
                    )

                def dismiss():
                    if self.get_mapped():
                        self.close()
                    return False

                GLib.idle_add(dismiss)

        self.view = OperationView(title, command, on_done=completed, on_close=self.close)
        self.view._heading.set_visible(False)  # The dialog header already names the operation.
        self.view.remove(self.view.footer)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.view.footer, f"set_margin_{side}")(12)
        toolbar.add_bottom_bar(self.view.footer)
        scroll.set_child(self.view)
