"""Offline AppStream mapping, cache and failure regression checks."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from orbit_gtk.backend.appstream import PackageMetadata
from orbit_gtk.backend.models import PackageInfo


def icon(kind, *, filename=None, name=None, width=64):
    result = MagicMock()
    result.get_kind.return_value = SimpleNamespace(value_nick=kind)
    result.get_filename.return_value = filename
    result.get_name.return_value = name
    result.get_width.return_value = width
    return result


class IconTests(unittest.TestCase):
    def test_maps_binary_packages_and_architectures_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            artwork = Path(directory) / "icon.png"
            artwork.touch()
            component = MagicMock()
            component.get_pkgnames.return_value = ["example", "example-extra"]
            component.get_icons.return_value = [
                icon("cached", filename=str(artwork)),
                icon("stock", name="org.example.App"),
            ]
            resolver = PackageMetadata()
            packages = [PackageInfo(name="example:i386"), PackageInfo(name="library")]
            with patch.object(resolver, "_load_components", return_value=[component]) as load:
                results = resolver.decorate(packages)
                self.assertEqual(results[0].icon_file, str(artwork))
                self.assertEqual(results[0].icon_name, "org.example.App")
                self.assertEqual(results[1].icon_file, "")
                self.assertEqual(packages[0].icon_file, "")
                self.assertEqual(resolver.decorate(packages), results)
                load.assert_called_once()
                resolver.invalidate()
                resolver.decorate(packages)
                self.assertEqual(load.call_count, 2)

    def test_ignores_remote_missing_and_relative_artwork(self):
        component = MagicMock()
        component.get_icons.return_value = [
            icon("remote", filename="https://example.org/icon.png"),
            icon("cached", filename="relative.png"),
            icon("local", filename="/nonexistent-orbit-icon.png"),
            icon("stock", name="../../bad"),
        ]
        self.assertEqual(PackageMetadata._component_icon(component), ("", ""))

    def test_missing_appstream_never_breaks_package_data(self):
        resolver = PackageMetadata()
        packages = [PackageInfo(name="example")]
        with patch.object(resolver, "_load_components", side_effect=ImportError):
            self.assertEqual(resolver.decorate(packages), packages)

    def test_prefers_row_sized_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            files = [Path(directory) / str(size) for size in (128, 64, 48)]
            for path in files:
                path.touch()
            component = MagicMock()
            component.get_icons.return_value = [
                icon("cached", filename=str(path), width=int(path.name)) for path in files
            ]
            self.assertEqual(PackageMetadata._component_icon(component)[0], str(files[1]))
