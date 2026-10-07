"""Plain-text row factories for untrusted package and repository metadata."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

_STATUS_STYLE = None
_EXPANDER_STYLE = None


def status_chip(text: str, color: str) -> Gtk.Label:
    global _STATUS_STYLE
    if _STATUS_STYLE is None:
        _STATUS_STYLE = Gtk.CssProvider()
        _STATUS_STYLE.load_from_string(
            ".status-chip { border-radius: 999px; padding: 4px 10px; "
            "background: alpha(currentColor, 0.12); font-weight: 600; }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _STATUS_STYLE, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class("status-chip")
    label.add_css_class(color)
    return label


def action_row(*, title: str = "", subtitle: str = "", **properties) -> Adw.ActionRow:
    # GObject construction may apply subtitle before use-markup. Set text only
    # after disabling markup, otherwise ampersands briefly produce GTK errors.
    row = Adw.ActionRow(**properties)
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def expander_row(*, title: str = "", subtitle: str = "", **properties) -> Adw.ExpanderRow:
    global _EXPANDER_STYLE
    if _EXPANDER_STYLE is None:
        _EXPANDER_STYLE = Gtk.CssProvider()
        # Adwaita's descendant selector also rotates collapsed nested arrows.
        # Target only the header belonging to this expander, preserving animation.
        header = "row.orbit-expander > box > list > row.header image.expander-row-arrow"
        expanded = "row.orbit-expander:checked > box > list > row.header image.expander-row-arrow"
        _EXPANDER_STYLE.load_from_string(
            f"{header} {{ -gtk-icon-transform: rotate(0.5turn); color: inherit; }}"
            f"{header}:dir(rtl) {{ -gtk-icon-transform: rotate(-0.5turn); }}"
            f"{expanded} {{ -gtk-icon-transform: rotate(0turn); }}"
            "row.history-transaction > box > revealer > list.nested, "
            "row.history-branch > box > revealer > list.nested { "
            "margin: 0 12px 12px 20px; padding-left: 10px; "
            "border-left: 2px solid alpha(currentColor, 0.22); background: transparent; }"
            "row.history-transaction > box > list > row.header .title { font-weight: 700; }"
            "row.history-branch > box > list > row.header .title { font-weight: 600; }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _EXPANDER_STYLE, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    row = Adw.ExpanderRow(**properties)
    row.add_css_class("orbit-expander")
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


_ICON_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="orbit-icons")


@lru_cache(maxsize=256)
def _load_texture(filename: str, modified: int):
    try:
        return Gdk.Texture.new_from_filename(filename)
    except GLib.Error:
        return None


def _apply_texture(image_ref, texture) -> bool:
    image = image_ref()
    if image is not None and texture is not None:
        image.set_from_paintable(texture)
    return False


def package_icon(package, size: int = 32) -> Gtk.Image:
    """Decode artwork on workers; only update live widgets on GTK's thread."""
    image = Gtk.Image(pixel_size=size, valign=Gtk.Align.CENTER)
    image.set_size_request(size, size)
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    name = package.icon_name
    image.set_from_icon_name(
        name if name and theme.has_icon(name) else "package-x-generic-symbolic"
    )
    if package.icon_file:
        try:
            modified = Path(package.icon_file).stat().st_mtime_ns
        except OSError:
            return image
        image_ref = image.weak_ref()
        future = _ICON_WORKERS.submit(_load_texture, package.icon_file, modified)
        future.add_done_callback(
            lambda loaded: GLib.idle_add(_apply_texture, image_ref, loaded.result())
        )
    return image
