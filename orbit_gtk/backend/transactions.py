"""Resolve, review and commit package changes through python-apt.

APT owns dependency resolution, authentication, downloads, dpkg and its locks.
No CLI output is parsed to decide whether a transaction succeeded.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

import apt
import apt_pkg

from orbit_gtk.backend.progress import CacheProgress, DownloadProgress, Emit, InstallProgress

PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?\Z")


class TransactionRejected(ValueError):
    """A resolved operation would violate Orbit's safety constraints."""


def configure_network() -> None:
    """Bound inactive connections and retry transient failures, respecting APT settings."""
    for key, value in {
        "Acquire::http::Timeout": "20",
        "Acquire::https::Timeout": "20",
        "Acquire::ftp::Timeout": "20",
        "Acquire::Retries": "2",
    }.items():
        if not apt_pkg.config.exists(key):
            apt_pkg.config.set(key, value)


def mark_changes(cache, action: str, names: Sequence[str]) -> list[dict]:
    """Mark exact package names and validate every dependency-induced change."""
    purging = set()
    reinstalling = set()
    autoremove_candidates = set()
    if action in {"upgrade", "full-upgrade"}:
        if names:
            raise TransactionRejected("Upgrade all does not accept package names.")
        cache.upgrade(dist_upgrade=action == "full-upgrade")
    elif action == "autoremove":
        if names:
            raise TransactionRejected("Autoremove does not accept package names.")
        candidates = [pkg for pkg in cache if pkg.is_installed and pkg.is_auto_removable]
        autoremove_candidates = {pkg.fullname for pkg in candidates}
        for pkg in candidates:
            pkg.mark_delete(auto_fix=False, purge=False)
    elif action in {"install", "remove", "purge", "reinstall"}:
        if not names:
            raise TransactionRejected("Select at least one package.")
        for name in dict.fromkeys(names):
            if not PACKAGE_NAME.fullmatch(name):
                raise TransactionRejected(f"Invalid package name: {name!r}")
            if name not in cache:
                raise TransactionRejected(
                    f"Package {name} is unavailable. Refresh package lists first."
                )
            pkg = cache[name]
            if pkg._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
                raise TransactionRejected(
                    f"{name} is held by APT. Release its hold before changing it."
                )
            if action == "reinstall":
                if not pkg.is_installed:
                    raise TransactionRejected(f"{name} is not installed.")
                version = pkg.versions[pkg.installed.version]
                if not version.downloadable:
                    raise TransactionRejected(
                        f"The installed version of {name} is no longer downloadable. Review an upgrade instead."
                    )
                pkg.candidate = version
                cache._depcache.set_reinstall(pkg._pkg, True)
                reinstalling.add(pkg.fullname)
            elif action == "install":
                if pkg.candidate is None:
                    raise TransactionRejected(f"No installation candidate for {name}.")
                pkg.mark_install(auto_fix=True, auto_inst=True, from_user=True)
            else:
                if not pkg.is_installed:
                    raise TransactionRejected(f"{name} is not installed.")
                pkg.mark_delete(auto_fix=True, purge=action == "purge")
                if action == "purge":
                    purging.add(pkg.fullname)
    else:
        raise TransactionRejected(f"Unsupported package operation: {action}")
    return describe_changes(cache, action, purging, reinstalling, autoremove_candidates)


def describe_changes(cache, action, purging=(), reinstalling=(), autoremove_candidates=()):
    """Validate every marked dependency change before exposing a review plan."""
    if cache.broken_count:
        raise TransactionRejected("Dependencies cannot be resolved. No changes were applied.")
    changes = []
    # python-apt's get_changes filters Keep mode, which can include packages
    # flagged for same-version reinstallation. Include those explicitly.
    marked = {pkg.fullname: pkg for pkg in cache.get_changes()}
    marked.update((name, cache[name]) for name in reinstalling)
    for pkg in sorted(marked.values(), key=lambda item: item.name):
        if pkg._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
            raise TransactionRejected(f"This operation would change held package {pkg.name}.")
        if action == "autoremove" and (
            not pkg.marked_delete or pkg.fullname not in autoremove_candidates
        ):
            raise TransactionRejected(
                "Autoremove would affect a package outside the unused dependency set."
            )
        if pkg.marked_delete:
            if action == "upgrade":
                raise TransactionRejected("A normal upgrade must not remove packages.")
            installed = pkg.installed
            protected = pkg.essential or (
                installed and installed.record.get("Protected", "no") == "yes"
            )
            if protected:
                raise TransactionRejected(
                    f"Refusing to remove essential or protected package {pkg.name}."
                )
            operation = "purge" if pkg.fullname in purging else "remove"
        elif pkg.marked_downgrade:
            raise TransactionRejected(f"Refusing an unrequested downgrade of {pkg.name}.")
        else:
            operation = (
                "reinstall"
                if pkg.fullname in reinstalling
                else "upgrade"
                if pkg.is_installed
                else "install"
            )
        changes.append(
            {
                "name": pkg.fullname,
                "action": operation,
                "old_version": pkg.installed.version if pkg.installed else None,
                "new_version": pkg.candidate.version
                if pkg.candidate and operation not in {"remove", "purge"}
                else None,
            }
        )
    return changes


def execute(action: str, names: Sequence[str], emit: Emit, approve: Callable[[dict], bool]) -> bool:
    """Keep the APT system lock from snapshot through review and commit."""
    configure_network()
    with apt_pkg.SystemLock():
        with apt.Cache(progress=CacheProgress(emit)) as cache:
            changes = mark_changes(cache, action, names)
            changed_names = {change["name"] for change in changes}
            kept_back = [
                pkg.fullname
                for pkg in cache
                if action in {"upgrade", "full-upgrade"}
                and pkg.is_upgradable
                and pkg.fullname not in changed_names
            ]
            if kept_back:
                emit("kept-back", packages=kept_back)
                emit(
                    "notice",
                    message=f"{len(kept_back)} updates kept back by APT: " + ", ".join(kept_back),
                )
            if not changes:
                emit(
                    "progress",
                    phase="Complete",
                    message="No package changes are needed.",
                    percent=100,
                )
                return True
            plan = {
                "changes": changes,
                "download_bytes": cache.required_download,
                "disk_bytes": cache.required_space,
                "kept_back": kept_back,
            }
            if not approve(plan):
                emit("cancelled", message="Cancelled before applying package changes.")
                return False
            downloads = {
                uri: pkg.fullname
                for pkg in (cache[change["name"]] for change in changes)
                if not pkg.marked_delete and pkg.candidate
                for uri in pkg.candidate.uris
            }
            with InstallProgress(emit, [change["name"] for change in changes]) as install_progress:
                try:
                    committed = cache.commit(
                        DownloadProgress(emit, downloads),
                        install_progress,
                        allow_unauthenticated=False,
                    )
                except apt.cache.FetchFailedException as error:
                    raise RuntimeError(
                        "Package download failed. Check your connection and the repository details, then retry.\n"
                        + str(error)
                    ) from error
                if not committed:
                    raise RuntimeError("APT did not complete the transaction.")
    return True


def update(emit: Emit) -> None:
    configure_network()
    # A partial refresh is not a successful refresh. APT retains usable old
    # indexes, but the GUI must not claim every repository was checked.
    apt_pkg.config.set("APT::Update::Error-Mode", "any")
    with apt.Cache(progress=CacheProgress(emit)) as cache:
        try:
            refreshed = cache.update(fetch_progress=DownloadProgress(emit), raise_on_error=True)
        except apt.cache.FetchFailedException as error:
            raise RuntimeError(
                "Repository refresh failed. Check your connection and repository details, then retry. "
                "You can still browse available cached package data.\n" + str(error)
            ) from error
        if not refreshed:
            raise RuntimeError("Some repository indexes could not be refreshed.")
