"""High-level backend facade used by Orbit's GTK pages."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from threading import local

from orbit_gtk.backend.appstream import PackageMetadata
from orbit_gtk.backend.apt_cache import AptCacheUnavailable, OrbitAptCache
from orbit_gtk.backend.history import load_transaction_history
from orbit_gtk.backend.mirrors import detect_system_suite, read_orbit_mirrors
from orbit_gtk.backend.models import CleanupItem, HistoryTransaction, PackageInfo


class AptManager:
    """Coordinate read-only APT data and commands delegated to the root helper."""

    def __init__(self) -> None:
        self._cache = OrbitAptCache()
        self._metadata = PackageMetadata()
        self._errors = local()

    @property
    def last_error(self) -> str | None:
        """The latest APT data-access error, suitable for a visible UI state."""
        return getattr(self._errors, "message", self._cache.error)

    def reload_cache(self) -> bool:
        """Reload the live APT cache after a successful transaction."""
        self._metadata.invalidate()
        success = self._cache.reload()
        self._errors.message = self._cache.error
        return success

    @staticmethod
    def helper_command(command: str, *extra_args: str) -> list[str]:
        """Build the fixed privileged helper invocation without shell interpolation."""
        project_root = Path(__file__).resolve().parents[2]
        if project_root == Path("/usr/lib/orbit-gtk"):
            return ["pkexec", "/usr/libexec/orbit-gtk/orbit-helper", command, *extra_args]
        bootstrap = (
            f"import sys; sys.path.insert(0, {str(project_root)!r}); "
            "from orbit_gtk.backend.helper import main; raise SystemExit(main())"
        )
        return ["pkexec", sys.executable, "-I", "-c", bootstrap, command, *extra_args]

    def get_installed_packages(self) -> list[PackageInfo]:
        return self._read_packages(self._cache.get_installed)

    def get_upgradable_packages(self) -> list[PackageInfo]:
        return self._read_packages(self._cache.get_upgradable)

    def get_package(self, name):
        return self._cache.get_package(name)

    def get_package_health(self):
        return self._cache.get_health()

    def search_packages(self, query: str, cancelled=None) -> list[PackageInfo]:
        return self._read_packages(lambda: self._cache.search(query, cancelled=cancelled))

    def _read_packages(self, operation) -> list[PackageInfo]:
        try:
            packages = operation()
        except (AptCacheUnavailable, OSError, SystemError) as error:
            self._errors.message = str(error)
            return []
        self._errors.message = None
        return self._metadata.decorate(packages)

    def get_history(self) -> list[HistoryTransaction]:
        return load_transaction_history()

    def get_system_info(self) -> dict[str, str]:
        """Return a coherent system identity for the dashboard and sidebar."""
        values: dict[str, str] = {}
        try:
            for line in (
                Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").splitlines()
            ):
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value.strip().strip('"')
        except OSError:
            pass

        distro_name = values.get("NAME") or values.get("ID") or "Debian"
        suite = detect_system_suite()
        return {
            "distro": f"{distro_name} ({suite})",
            "distro_name": distro_name,
            "suite": suite,
            "kernel": platform.release(),
            "arch": platform.machine(),
        }

    def get_orbit_mirrors(self) -> list[str]:
        """Read the source file owned by Orbit; never present user sources as editable."""
        return read_orbit_mirrors()

    def get_cleanup_items(self) -> list[CleanupItem]:
        """Calculate the two cleanup categories the privileged helper can safely handle."""
        definitions = (
            (
                "apt_cache",
                "Downloaded package cache",
                "Downloaded .deb archives in /var/cache/apt/archives",
                Path("/var/cache/apt/archives"),
                lambda path: path.suffix == ".deb",
                True,
            ),
            (
                "apt_lists",
                "Repository package lists",
                "APT index files; selecting this requires a later package-list refresh",
                Path("/var/lib/apt/lists"),
                lambda path: path.name not in {"lock", "partial"} and path.is_file(),
                False,
            ),
        )
        items: list[CleanupItem] = []
        for key, title, description, directory, include, selected in definitions:
            try:
                paths = [path for path in directory.iterdir() if include(path) and path.is_file()]
            except FileNotFoundError:
                continue
            sizes = []
            for path in paths:
                try:
                    sizes.append(path.stat().st_size)
                except FileNotFoundError:
                    continue  # APT may remove archives while the UI reads them.
            size = sum(sizes)
            if size:
                items.append(
                    CleanupItem(
                        key=key,
                        title=title,
                        description=description,
                        count=len(sizes),
                        size_bytes=size,
                        location=os.fspath(directory),
                        is_selected=selected,
                    )
                )
        return items

    @staticmethod
    def format_size(size_bytes: int | float) -> str:
        """Format a byte count consistently across all pages."""
        value = max(0, float(size_bytes))
        units = ("B", "KiB", "MiB", "GiB", "TiB")
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        raise AssertionError("unreachable")
