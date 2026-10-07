"""Native controls for debconf and dpkg configuration questions."""

from gi.repository import Adw, Gtk

from orbit_gtk.i18n import tr


def present_question(parent, question, respond):
    dialog = Adw.Dialog(
        title=question.get("title", "Package configuration"), content_width=660, content_height=500
    )
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    dialog.set_child(toolbar)
    page = Adw.PreferencesPage()
    toolbar.set_content(page)
    group = Adw.PreferencesGroup(title=question.get("description", "Configuration"))
    page.add(group)
    details = Gtk.Label(label=question.get("details", ""), wrap=True, selectable=True, xalign=0)
    group.add(details)
    kind, default = question.get("kind"), question.get("default", "")
    choices = [v.strip() for v in question.get("choices", "").split(",") if v.strip()]
    if kind == "boolean":
        control = Gtk.CheckButton(label=tr("Yes"), active=default == "true")

        def value():
            return "true" if control.get_active() else "false"
    elif kind == "select" and choices:
        control = Gtk.DropDown(model=Gtk.StringList.new(choices))
        control.set_selected(choices.index(default) if default in choices else 0)

        def value():
            return choices[control.get_selected()]
    elif kind == "multiselect":
        control = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        checks = []
        for choice in choices:
            check = Gtk.CheckButton(label=choice, active=choice in default.split(", "))
            checks.append(check)
            control.append(check)

        def value():
            return ", ".join(c.get_label() for c in checks if c.get_active())
    elif kind in {"note", "text", "error"}:
        control = Gtk.Label(label=tr("Acknowledge this message to continue."), wrap=True)

        def value():
            return default
    else:
        control = Gtk.Entry(text=default, visibility=kind != "password", activates_default=True)
        value = control.get_text
    group.add(control)
    button = Gtk.Button(label=tr("Continue"), halign=Gtk.Align.END)
    group.add(button)
    answered = False

    def finish(_):
        nonlocal answered
        if not answered:
            answered = True
            respond(value())
        dialog.close()

    def closed(_):
        nonlocal answered
        if not answered:
            answered = True
            respond(default)

    button.connect("clicked", finish)
    dialog.connect("closed", closed)
    dialog.present(parent)
    return dialog
