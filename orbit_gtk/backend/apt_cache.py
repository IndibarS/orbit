"""Thread-safe, read-only views of python-apt's package cache."""

from __future__ import annotations

import logging
from dataclasses import replace
from heapq import nsmallest
from threading import RLock

from orbit_gtk.backend.models import PackageHealth, PackageInfo

LOG = logging.getLogger(__name__)

try:
    import apt
    import apt_pkg
except ImportError:  # Allows the rest of the app to show a useful dependency error.
    apt = None
    apt_pkg = None


class AptCacheUnavailable(RuntimeError):
    """Raised internally when the distro-provided python-apt bindings are unavailable."""


class OrbitAptCache:
    """Expose stable package snapshots without inventing package data on failure."""

    def __init__(self) -> None:
        self._cache = None
        self._error: str | None = None
        self._lock = RLock()
        self._initialized = False
        self._snapshots: dict[str, list[PackageInfo]] = {}

    @property
    def is_available(self) -> bool:
        return self._cache is not None

    @property
    def error(self) -> str | None:
        return self._error

    def reload(self) -> bool:
        """Open a fresh APT cache and retain a human-readable error on failure."""
        with self._lock:
            self._initialized = True
            self._snapshots.clear()
            if apt is None:
                self._cache = None
                self._error = "python-apt is not installed for this Python interpreter."
                return False
            try:
                cache = apt.Cache()
            except Exception as error:  # python-apt uses several implementation-specific errors.
                self._cache = None
                self._error = f"Unable to open the APT cache: {error}"
                LOG.warning("%s", self._error)
                return False
            self._cache = cache
            self._error = None
            return True

    def get_installed(self) -> list[PackageInfo]:
        """Return installed real packages, sorted once by package name."""
        with self._lock:
            cache = self._require_cache()
            if "installed" not in self._snapshots:
                self._snapshots["installed"] = sorted(
                    (self._package_info(pkg) for pkg in cache if pkg.is_installed),
                    key=lambda package: package.name.casefold(),
                )
            return list(self._snapshots["installed"])

    def get_upgradable(self) -> list[PackageInfo]:
        """Return candidates with the same kept-back policy as normal upgrades."""
        with self._lock:
            cache = self._require_cache()
            if "upgradable" not in self._snapshots:
                try:
                    cache.upgrade(dist_upgrade=False)
                    changing = {pkg.fullname for pkg in cache.get_changes()}
                    packages = []
                    for pkg in cache:
                        if not pkg.is_upgradable:
                            continue
                        info = self._package_info(pkg)
                        if pkg.fullname not in changing and not info.is_held_back:
                            info = replace(
                                info,
                                is_held_back=True,
                                held_reason="Kept back by APT’s normal upgrade policy",
                            )
                        packages.append(info)
                    self._snapshots["upgradable"] = sorted(
                        packages, key=lambda pkg: pkg.name.casefold()
                    )
                except Exception as error:
                    raise AptCacheUnavailable(
                        f"Unable to resolve normal upgrades: {error}"
                    ) from error
                finally:
                    # Simulation changes only the in-memory dependency cache.
                    cache.clear()
            return list(self._snapshots["upgradable"])

    def search(self, query: str, limit: int = 200, cancelled=None) -> list[PackageInfo]:
        """Find real packages by name, summary, or description with deterministic ranking."""
        normalized = query.strip().casefold()
        if not normalized:
            return []
        with self._lock:
            cache = self._require_cache()
            matches = []
            for package in cache:
                if cancelled and cancelled():
                    return []
                if not self._is_real_package(package):
                    continue
                version = package.candidate or package.installed
                if version is None:
                    continue
                name = package.name.casefold()
                if normalized == name:
                    rank = 0
                elif name.startswith(normalized):
                    rank = 1
                elif normalized in name:
                    rank = 2
                elif normalized in (version.summary or "").casefold():
                    rank = 3
                elif normalized in (version.description or "").casefold():
                    rank = 4
                else:
                    continue
                matches.append((rank, name, package))
            best = nsmallest(max(0, limit), matches, key=lambda item: item[:2])
            return [self._package_info(package) for _, _, package in best]

    def get_health(self) -> PackageHealth:
        """Inspect dpkg states without executing a repair or assuming rollback."""
        with self._lock:
            cache = self._require_cache()
            pending, reinstall = [], []
            settled = {
                apt_pkg.CURSTATE_NOT_INSTALLED,
                apt_pkg.CURSTATE_CONFIG_FILES,
                apt_pkg.CURSTATE_INSTALLED,
            }
            for package in cache:
                raw = package._pkg
                if (
                    raw.inst_state
                    in {apt_pkg.INSTSTATE_REINSTREQ, apt_pkg.INSTSTATE_HOLD_REINSTREQ}
                    or raw.current_state == apt_pkg.CURSTATE_HALF_INSTALLED
                ):
                    reinstall.append(package.fullname)
                elif raw.current_state not in settled:
                    # Includes unpacked, half-configured and trigger states;
                    # python-apt does not expose named constants for all of them.
                    pending.append(package.fullname)
            return PackageHealth(
                tuple(sorted(pending)), tuple(sorted(reinstall)), cache.broken_count
            )

    def _require_cache(self):
        if not self._initialized:
            self.reload()
        if self._cache is None:
            raise AptCacheUnavailable(self._error or "The APT cache is unavailable.")
        return self._cache

    @staticmethod
    def _is_real_package(package) -> bool:
        """Apply Nala's useful virtual-package filtering to search results."""
        raw_package = getattr(package, "_pkg", None)
        return bool(raw_package and raw_package.has_versions and not package.name.startswith("$"))

    @staticmethod
    def _origin_name(version) -> str:
        # Constructing apt.package.Origin verifies trust for every source on
        # every package. For a display label use the cached PackageFile fields,
        # as Nala's search renderer does; commit still verifies authentication.
        for package_file, _ in version._cand.file_list:
            name = package_file.origin or package_file.label
            if name:
                return name
        return "Local / unknown"

    @staticmethod
    def _is_held(package) -> bool:
        raw_package = getattr(package, "_pkg", None)
        return bool(
            raw_package is not None
            and apt_pkg is not None
            and raw_package.selected_state == apt_pkg.SELSTATE_HOLD
        )

    def _package_info(self, package) -> PackageInfo:
        candidate = package.candidate
        installed = package.installed
        version = candidate or installed
        assert version is not None
        held = self._is_held(package)
        return PackageInfo(
            name=package.name,
            full_name=package.fullname,
            summary=version.summary or "",
            description=version.description or version.summary or "",
            installed_version=installed.version if installed else None,
            latest_version=candidate.version
            if candidate
            else (installed.version if installed else None),
            size_bytes=installed.installed_size if installed else version.installed_size,
            download_size_bytes=candidate.size if candidate else 0,
            source=self._origin_name(version),
            section=version.section or "",
            homepage=version.homepage or "",
            maintainer=getattr(version, "maintainer", "") or "",
            priority=version.priority or "optional",
            is_upgradable=package.is_upgradable,
            is_installed=package.is_installed,
            is_orphaned=bool(package.is_auto_removable),
            is_manual=package.is_installed and not package.is_auto_installed,
            is_held=held,
            is_held_back=package.is_upgradable and held,
            held_reason="Explicitly held by APT" if held else "",
        )
