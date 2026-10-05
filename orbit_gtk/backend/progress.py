"""APT callbacks translated into structured events, independent of GTK and locale.

Nala's legacy dpkg.py uses these same python-apt progress interfaces. Orbit
uses the library callbacks directly instead of importing Nala's terminal state.
"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from apt.progress import base

Emit = Callable[..., None]


class CacheProgress(base.OpProgress):
    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self._last = 0.0

    def update(self, percent: float | None = None) -> None:
        super().update(percent)
        now = monotonic()
        if now - self._last >= 0.1:
            self._last = now
            self.emit(
                "progress", phase="Reading package lists", message=self.op, percent=self.percent
            )


class DownloadProgress(base.AcquireProgress):
    def __init__(self, emit: Emit, packages: dict[str, str] | None = None) -> None:
        super().__init__()
        self.emit = emit
        self.description = "Connecting to repositories…"
        self.packages = packages or {}

    def fetch(self, item) -> None:
        self.description = item.description
        self._package_event(item, "Downloading…", None)

    def _package_event(self, item, message: str, percent: float | None) -> None:
        package = self.packages.get(item.uri)
        if package:
            self.emit("package-progress", package=package, message=message, percent=percent)

    def done(self, item) -> None:
        self._package_event(item, "Downloaded", 100)

    def ims_hit(self, item) -> None:
        self.description = f"Up to date: {item.description}"

    def fail(self, item) -> None:
        # APT can retry failed items; cache.update/commit determines final failure.
        self.emit("warning", message=f"{item.description}: {item.owner.error_text}")

    def pulse(self, owner) -> bool:
        for worker in getattr(owner, "workers", ()):
            if worker.current_item:
                percent = (
                    min(100, worker.current_size / worker.total_size * 100)
                    if worker.total_size
                    else None
                )
                message = f"Downloading · {percent:.0f}%" if percent is not None else "Downloading…"
                self._package_event(worker.current_item, message, percent)
        total = self.total_bytes + self.total_items
        current = self.current_bytes + self.current_items
        self.emit(
            "progress",
            phase="Downloading",
            message=self.description,
            percent=min(100.0, current / total * 100) if total else None,
            bytes=int(self.current_bytes),
            total_bytes=int(self.total_bytes),
            bytes_per_second=int(self.current_cps),
        )
        return True

    def media_change(self, media: str, drive: str) -> bool:
        self.emit("error", message=f"Repository requires removable media: {media} ({drive}).")
        return False


class InstallProgress(base.InstallProgress):
    def __init__(self, emit: Emit, packages=()) -> None:
        super().__init__()
        self.emit = emit
        self.packages = set(packages)
        self._aliases = {}
        for key in self.packages:
            self._aliases.setdefault(key.split(":", 1)[0], []).append(key)

    def _package_name(self, name, status):
        if name in self.packages:
            return name
        matches = self._aliases.get(name, [])
        if len(matches) == 1:
            return matches[0]
        # APT's pmstatus can omit the architecture from its package field,
        # but include it in the status text. Never guess between architectures.
        qualified = [key for key in matches if status.endswith(f"{name} ({key.split(':', 1)[1]})")]
        return qualified[0] if len(qualified) == 1 else name

    def start_update(self) -> None:
        self.emit("progress", phase="Installing", message="Preparing packages…", percent=0)

    def status_change(self, pkg: str, percent: float, status: str) -> None:
        self.emit(
            "progress",
            phase="Installing",
            package=self._package_name(pkg, status),
            stage_complete=status.startswith(("Installed ", "Removed ", "Completely removed ")),
            message=status,
            percent=percent,
        )

    def error(self, pkg: str, errormsg: str) -> None:
        self.emit("error", message=f"{pkg}: {errormsg}")

    def conffile(self, current: str, new: str) -> None:
        self.emit("warning", message=f"Keeping local configuration {current}; new version: {new}")
