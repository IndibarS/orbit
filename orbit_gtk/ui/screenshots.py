"""On-demand screenshot gallery with worker loading and stale-result protection."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from gi.repository import Adw, Gdk, GLib, Gtk

from orbit_gtk.backend.screenshots import fetch_screenshot
from orbit_gtk.i18n import tr

_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="orbit-screenshots")


@lru_cache(maxsize=8)
def _load(url):
    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(fetch_screenshot(url)))
    if texture.get_width() > 4096 or texture.get_height() > 4096:
        raise ValueError("Screenshot dimensions are too large")
    return texture


def _deliver(reference, generation, future):
    gallery = reference()
    if gallery is not None and generation == gallery._generation:
        try:
            gallery._picture.set_paintable(future.result())
        except Exception:
            gallery._stack.set_visible_child_name("error")
        else:
            gallery._stack.set_visible_child_name("image")
        gallery._spinner.stop()
    return False


class ScreenshotGallery(Adw.PreferencesGroup):
    def __init__(self, screenshots):
        super().__init__(title=tr("Screenshots"))
        self._screenshots = screenshots
        self._index = 0
        self._generation = 0
        self._future = None
        self._stack = Gtk.Stack()
        self._picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
        self._stack.add_named(self._picture, "image")
        self._spinner = Gtk.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._stack.add_named(self._spinner, "loading")
        self._stack.set_visible_child_name("loading")
        error = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        error.append(Gtk.Label(label=tr("Screenshot unavailable"), css_classes=["dim-label"]))
        error.append(
            Gtk.Label(
                label=tr("Check your connection and try again. Package details remain available."),
                wrap=True,
            )
        )
        retry = Gtk.Button(label=tr("Retry"), halign=Gtk.Align.CENTER)
        retry.connect("clicked", lambda _: self._show())
        error.append(retry)
        self._stack.add_named(error, "error")
        self._frame = Gtk.AspectFrame(ratio=16 / 10, obey_child=False)
        self._frame.set_size_request(-1, 260)
        self._frame.set_child(self._stack)
        self._frame.add_css_class("card")
        self.add(self._frame)
        self._caption = Gtk.Label(wrap=True, xalign=0, margin_top=8, selectable=True)
        self._caption.add_css_class("caption")
        self.add(self._caption)
        controls = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self._previous = Gtk.Button(
            icon_name="go-previous-symbolic", tooltip_text=tr("Previous screenshot")
        )
        self._previous.connect("clicked", lambda _: self._move(-1))
        self._counter = Gtk.Label()
        self._next = Gtk.Button(icon_name="go-next-symbolic", tooltip_text=tr("Next screenshot"))
        self._next.connect("clicked", lambda _: self._move(1))
        for widget in (self._previous, self._counter, self._next):
            controls.append(widget)
        self.set_header_suffix(controls)
        self.connect("map", self._map)
        self.connect("unmap", self._unmap)

    def _map(self, _widget):
        if self._future is None:
            self._show()

    def _unmap(self, _widget):
        self._generation += 1
        if self._future:
            self._future.cancel()
            self._future = None
        self._spinner.stop()

    def _move(self, step):
        self._index = max(0, min(len(self._screenshots) - 1, self._index + step))
        self._show()

    def _show(self):
        self._generation += 1
        if self._future:
            self._future.cancel()
        shot = self._screenshots[self._index]
        self._counter.set_label(f"{self._index + 1} / {len(self._screenshots)}")
        self._previous.set_sensitive(self._index > 0)
        self._next.set_sensitive(self._index + 1 < len(self._screenshots))
        self._caption.set_label(shot.caption)
        self._caption.set_visible(bool(shot.caption))
        self._picture.set_alternative_text(
            shot.caption or f"Application screenshot {self._index + 1}"
        )
        self._frame.set_ratio(max(0.6, min(2.5, shot.width / shot.height)) if shot.height else 1.6)
        self._stack.set_visible_child_name("loading")
        self._spinner.start()
        self._future = _WORKERS.submit(_load, shot.url)
        reference, generation = self.weak_ref(), self._generation
        self._future.add_done_callback(
            lambda result: GLib.idle_add(_deliver, reference, generation, result)
        )
