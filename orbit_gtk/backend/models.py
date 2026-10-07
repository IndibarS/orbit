"""Typed snapshots passed between workers and GTK; no synthetic metrics."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PackageHealth:
    pending_configuration: tuple[str, ...] = ()
    reinstall_required: tuple[str, ...] = ()
    broken_dependencies: int = 0

    @property
    def needs_attention(self) -> bool:
        return bool(
            self.pending_configuration or self.reinstall_required or self.broken_dependencies
        )


@dataclass(frozen=True, slots=True)
class ScreenshotInfo:
    url: str
    caption: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True, slots=True)
class PackageInfo:
    name: str
    full_name: str = ""
    icon_file: str = ""
    icon_name: str = ""
    screenshots: tuple[ScreenshotInfo, ...] = ()
    summary: str = ""
    description: str = ""
    installed_version: str | None = None
    latest_version: str | None = None
    size_bytes: int = 0
    download_size_bytes: int = 0
    source: str = "Unknown"
    section: str = ""
    homepage: str = ""
    maintainer: str = ""
    priority: str = "optional"
    is_upgradable: bool = False
    is_installed: bool = False
    is_orphaned: bool = False
    is_manual: bool = False
    is_held: bool = False
    is_held_back: bool = False
    held_reason: str = ""
    providers: tuple[str, ...] = ()
    versions: tuple[tuple[str, str, int, bool], ...] = ()
    relations: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class HistoryTransaction:
    id: str
    date: str
    requested_by: str
    command: str
    operation: str
    altered_count: int
    status: str = "Recorded"
    upgraded_pkgs: list[PackageInfo] = field(default_factory=list)
    installed_pkgs: list[PackageInfo] = field(default_factory=list)
    removed_pkgs: list[PackageInfo] = field(default_factory=list)
    purged_pkgs: list[PackageInfo] = field(default_factory=list)
    reinstalled_pkgs: list[PackageInfo] = field(default_factory=list)
    downgraded_pkgs: list[PackageInfo] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MirrorInfo:
    domain: str
    url: str
    country_code: str
    latency_ms: float

    @property
    def reachable(self) -> bool:
        from math import isfinite

        return isfinite(self.latency_ms)


@dataclass(slots=True)
class CleanupItem:
    key: str
    title: str
    description: str
    count: int
    size_bytes: int
    location: str
    is_selected: bool = True
