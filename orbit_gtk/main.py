"""Application entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Create the Adwaita application lazily so backend tests need no GTK display."""
    from orbit_gtk.cli import parse_command

    arguments = argv if argv is not None else sys.argv
    try:
        parse_command(arguments[1:])
    except SystemExit as result:
        return result.code
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio

    from orbit_gtk.backend.apt_manager import AptManager
    from orbit_gtk.ui.window import OrbitWindow

    app = Adw.Application(
        application_id="io.github.orbit_package_manager.Orbit",
        flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
    )

    def on_activate(application: Adw.Application) -> None:
        window = application.get_active_window()
        if window is None:
            window = OrbitWindow(application=application, apt_manager=AptManager())
        window.present()

    def on_command_line(application, command_line):
        request = parse_command(command_line.get_arguments()[1:], command_line.get_cwd())
        on_activate(application)
        application.get_active_window().dispatch_command(request)
        return 0

    app.connect("command-line", on_command_line)
    app.connect("activate", on_activate)
    return app.run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
