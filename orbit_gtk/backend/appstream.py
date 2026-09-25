"""Local application artwork metadata from the system AppStream catalogue."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from threading import RLock

from orbit_gtk.backend.models import PackageInfo, ScreenshotInfo
from orbit_gtk.backend.screenshots import valid_image_url

LOG = logging.getLogger(__name__)


class PackageMetadata:
    """Build once on a data worker; never fetch URLs or guess package icon names."""

    def __init__(self):
        self._lock = RLock()
        self._index: dict[str, tuple[str, str, tuple[ScreenshotInfo, ...]]] | None = None

    def invalidate(self):
        with self._lock:
            self._index = None

    @staticmethod
    def _load_components():
        import gi

        gi.require_version("AppStream", "1.0")
        from gi.repository import AppStream

        pool = AppStream.Pool()
        # Exclude Flatpak: these rows represent Debian packages only.
        pool.set_flags(AppStream.PoolFlags.LOAD_OS_CATALOG | AppStream.PoolFlags.LOAD_OS_METAINFO)
        pool.load(None)
        return pool.get_components().as_array()

    @staticmethod
    def _component_icon(component) -> tuple[str, str]:
        files = []
        stock = ""
        for icon in component.get_icons():
            kind = icon.get_kind().value_nick
            if kind == "stock":
                name = icon.get_name() or ""
                if name and "/" not in name:
                    stock = name
            elif kind in {"cached", "local"}:
                filename = icon.get_filename()
                if filename and Path(filename).is_absolute() and Path(filename).is_file():
                    files.append((abs(icon.get_width() - 64), filename))
        return (min(files)[1] if files else "", stock)

    @staticmethod
    def _component_screenshots(component) -> tuple[ScreenshotInfo, ...]:
        result = []
        for shot in component.get_screenshots_all():
            images = [
                image
                for image in shot.get_images()
                if valid_image_url(image.get_url() or "")
                and 0 < image.get_width() <= 4096
                and 0 < image.get_height() <= 4096
            ]
            if not images:
                continue
            image = min(images, key=lambda item: abs(item.get_width() - 752))
            if any(item.url == image.get_url() for item in result):
                continue
            result.append(
                ScreenshotInfo(
                    image.get_url(), shot.get_caption() or "", image.get_width(), image.get_height()
                )
            )
            if len(result) == 12:
                break
        return tuple(result)

    def decorate(self, packages: list[PackageInfo]) -> list[PackageInfo]:
        if not packages:
            return packages
        with self._lock:
            if self._index is None:
                self._index = {}
                try:
                    for component in self._load_components():
                        icon = (
                            *self._component_icon(component),
                            self._component_screenshots(component),
                        )
                        if not any(icon):
                            continue
                        for name in component.get_pkgnames() or []:
                            self._index.setdefault(name, icon)
                except (ImportError, ValueError, RuntimeError, OSError) as error:
                    LOG.info("Application artwork metadata unavailable: %s", error)
                except Exception as error:
                    # GI raises GLib.Error for missing/unreadable catalogue caches.
                    # Icons must never make package management unavailable.
                    LOG.warning("Unable to load AppStream artwork metadata: %s", error)
            result = []
            for package in packages:
                name = (package.full_name or package.name).split(":", 1)[0]
                filename, stock, screenshots = self._index.get(name, ("", "", ()))
                result.append(
                    replace(package, icon_file=filename, icon_name=stock, screenshots=screenshots)
                )
            return result
