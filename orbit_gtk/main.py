"""Application entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Create the Adwaita application lazily so backend tests need no GTK display."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from orbit_gtk.backend.apt_manager import AptManager
    from orbit_gtk.ui.window import OrbitWindow

    app = Adw.Application(application_id="io.github.orbit_package_manager.Orbit")

    def on_activate(application: Adw.Application) -> None:
        window = application.get_active_window()
        if window is None:
            window = OrbitWindow(application=application, apt_manager=AptManager())
        window.present()

    app.connect("activate", on_activate)
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
