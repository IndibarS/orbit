"""A cancellable URL download leading to the normal local-package review."""

import tempfile
import threading
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from orbit_gtk.backend.archive_download import download_archive
from orbit_gtk.i18n import tr


class ArchiveDownload(Adw.Dialog):
    def __init__(self, window, url="", sha256=None):
        super().__init__(title=tr("Download a Debian package"), content_width=580)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        group = Adw.PreferencesGroup(
            description=tr(
                "Download from a trusted source. The archive will be reviewed before installation; repository signatures do not authenticate it."
            )
        )
        page.add(group)
        entry = Adw.EntryRow(title=tr("Package URL"))
        entry.set_text(url)
        group.add(entry)
        checksum = Adw.EntryRow(title=tr("Expected SHA256 · optional"))
        checksum.set_text(sha256 or "")
        group.add(checksum)
        bar = Gtk.ProgressBar(show_text=True)
        group.add(bar)
        status = Gtk.Label(wrap=True)
        group.add(status)
        start = Gtk.Button(label=tr("Download and review"))
        group.add(start)
        self.cancelled = threading.Event()
        cancel = Gtk.Button(label=tr("Cancel download"), visible=False)
        cancel.connect("clicked", lambda _: self.cancelled.set())
        group.add(cancel)
        self.connect("closed", lambda *_: self.cancelled.set())

        def progress(current, total):
            if total:
                bar.set_fraction(current / total)
            else:
                bar.pulse()
            bar.set_text(
                f"{current / 1048576:.1f} MiB" + (f" / {total / 1048576:.1f} MiB" if total else "")
            )
            return False

        def complete(path, directory, error):
            start.set_sensitive(True)
            cancel.set_visible(False)
            if error or self.cancelled.is_set():
                directory.cleanup()
                status.set_label(error or "Download cancelled")
                return False
            window._downloaded_archives.append(directory)
            self.close()
            window.install_local(str(path))
            return False

        def download(_):
            link, expected = entry.get_text().strip(), checksum.get_text().strip()
            if expected and (
                len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected)
            ):
                status.set_label(tr("SHA256 must contain 64 hexadecimal characters"))
                return
            self.cancelled.clear()
            start.set_sensitive(False)
            cancel.set_visible(True)
            status.set_label(tr("Downloading…"))

            def run():
                directory = tempfile.TemporaryDirectory(prefix="orbit-download-")
                path = Path(directory.name) / "package.deb"
                try:
                    download_archive(
                        link,
                        path,
                        cancelled=self.cancelled.is_set,
                        progress=lambda a, b: GLib.idle_add(progress, a, b),
                        sha256=expected or None,
                    )
                    error = None
                except Exception as failure:
                    error = str(failure)
                GLib.idle_add(complete, path, directory, error)

            threading.Thread(target=run, daemon=True).start()

        start.connect("clicked", download)
