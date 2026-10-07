"""Resolve, review and commit package changes through python-apt.

APT owns dependency resolution, authentication, downloads, dpkg and its locks.
No CLI output is parsed to decide whether a transaction succeeded.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from fnmatch import fnmatchcase

import apt
import apt_pkg

from orbit_gtk.backend.progress import CacheProgress, DownloadProgress, Emit, InstallProgress
from orbit_gtk.backend.transaction_options import validate_options

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


def mark_changes(
    cache, action: str, names: Sequence[str], options=None, _defer=False
) -> list[dict]:
    """Mark exact package names and validate every dependency-induced change."""
    options = validate_options(options)
    if action == "batch":
        requests = options.get("requests", [])
        if not requests:
            raise TransactionRejected("The package selection is empty.")
        seen, purges, reinstalls, downgrades = set(), set(), set(), set()
        for request in requests:
            name = request["name"]
            if name in seen:
                raise TransactionRejected(f"Conflicting duplicate selection: {name}")
            seen.add(name)
            if "expected" in request:
                installed = cache[name].installed if name in cache else None
                if (installed.version if installed else None) != request["expected"]:
                    raise TransactionRejected(
                        f"{name} has changed since this history entry. Review it individually."
                    )
            item_options = (
                {"versions": {name: request["version"]}} if request.get("version") else {}
            )
            p, r, d = mark_changes(cache, request["action"], [name], item_options, _defer=True)
            purges.update(p)
            reinstalls.update(r)
            downgrades.update(d)
        return describe_changes(cache, "batch", purges, reinstalls, explicit_downgrades=downgrades)
    requested_versions = options.get("versions", {})
    if requested_versions and (action != "install" or not set(requested_versions).issubset(names)):
        raise TransactionRejected("Versions must refer to explicitly requested installations.")
    explicit_downgrades = set()
    excluded = (
        {
            pkg.fullname
            for pkg in cache
            if any(
                fnmatchcase(pkg.name, pattern) or fnmatchcase(pkg.fullname, pattern)
                for pattern in options.get("exclude", [])
            )
        }
        if options.get("exclude")
        else set()
    )
    if excluded and action not in {"upgrade", "full-upgrade"}:
        raise TransactionRejected("Exclusions apply only to upgrades.")
    purging = set()
    reinstalling = set()
    autoremove_candidates = set()
    if action in {"upgrade", "full-upgrade"}:
        if names:
            raise TransactionRejected("Upgrade all does not accept package names.")
        cache.upgrade(dist_upgrade=action == "full-upgrade")
        for name in excluded:
            cache[name].mark_keep()
    elif action == "fix-broken":
        if names:
            raise TransactionRejected("Dependency repair does not accept package names.")
        if not cache._depcache.fix_broken():
            raise TransactionRejected("APT could not resolve broken dependencies.")
    elif action in {"autoremove", "autopurge", "purge-config"}:
        if names:
            raise TransactionRejected("Autoremove does not accept package names.")
        candidates = [
            pkg
            for pkg in cache
            if (
                getattr(pkg._pkg, "current_state", None) == apt_pkg.CURSTATE_CONFIG_FILES
                if action == "purge-config"
                else pkg.is_installed and pkg.is_auto_removable
            )
        ]
        autoremove_candidates = {pkg.fullname for pkg in candidates}
        for pkg in candidates:
            purge = action != "autoremove"
            pkg.mark_delete(auto_fix=False, purge=purge)
            if purge:
                purging.add(pkg.fullname)
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
                if name in requested_versions:
                    try:
                        pkg.candidate = pkg.versions[requested_versions[name]]
                    except (KeyError, IndexError):
                        raise TransactionRejected(
                            f"Requested version of {name} is unavailable."
                        ) from None
                    if (
                        pkg.installed
                        and apt_pkg.version_compare(pkg.candidate.version, pkg.installed.version)
                        < 0
                    ):
                        explicit_downgrades.add(pkg.fullname)
                if pkg.candidate is None:
                    raise TransactionRejected(f"No installation candidate for {name}.")
                pkg.mark_install(auto_fix=True, auto_inst=True, from_user=True)
            else:
                residual = getattr(pkg._pkg, "current_state", None) == apt_pkg.CURSTATE_CONFIG_FILES
                if not pkg.is_installed and not (action == "purge" and residual):
                    raise TransactionRejected(f"{name} is not installed.")
                pkg.mark_delete(auto_fix=True, purge=action == "purge")
                if action == "purge":
                    purging.add(pkg.fullname)
    else:
        raise TransactionRejected(f"Unsupported package operation: {action}")
    if options.get("autoremove"):
        for pkg in cache:
            if pkg.is_installed and pkg.is_auto_removable:
                pkg.mark_delete(auto_fix=False, purge=options.get("purge", False))
    if options.get("purge"):
        for pkg in cache.get_changes():
            if pkg.marked_delete:
                pkg.mark_delete(auto_fix=False, purge=True)
                purging.add(pkg.fullname)
    if _defer:
        return purging, reinstalling, explicit_downgrades
    changes = describe_changes(
        cache, action, purging, reinstalling, autoremove_candidates, explicit_downgrades
    )
    if excluded & {change["name"] for change in changes}:
        raise TransactionRejected(
            "Dependencies would change an excluded package. Adjust the selection."
        )
    return changes


def describe_changes(
    cache, action, purging=(), reinstalling=(), autoremove_candidates=(), explicit_downgrades=()
):
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
        if action in {"autoremove", "autopurge", "purge-config"} and (
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
            if pkg.fullname not in explicit_downgrades:
                raise TransactionRejected(f"Refusing an unrequested downgrade of {pkg.name}.")
            operation = "downgrade"
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


def execute(
    action: str,
    names: Sequence[str],
    emit: Emit,
    approve: Callable[[dict], bool],
    options=None,
    cancelled=None,
    question=None,
) -> bool:
    """Keep the APT system lock from snapshot through review and commit."""
    options = validate_options(options)
    configure_network()
    for key, apt_key in (
        ("recommends", "APT::Install-Recommends"),
        ("suggests", "APT::Install-Suggests"),
    ):
        if key in options:
            apt_pkg.config.set(apt_key, str(options[key]).lower())
    if options.get("target_release"):
        apt_pkg.config.set("APT::Default-Release", options["target_release"])
    if options.get("refresh"):
        update(emit)
    with apt_pkg.SystemLock():
        with apt.Cache(progress=CacheProgress(emit)) as cache:
            changes = mark_changes(cache, action, names, options)
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
                "download_only": options.get("download_only", False),
                "conffile": options.get("conffile", "keep"),
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
            emit("cancellable", enabled=True)
            try:
                cache.fetch_archives(
                    progress=DownloadProgress(emit, downloads, cancelled),
                    allow_unauthenticated=False,
                )
                if cancelled and cancelled():
                    raise apt.cache.FetchCancelledException("Cancelled")
            except apt.cache.FetchFailedException as error:
                raise RuntimeError(
                    "Package download failed. Check your connection and retry.\n" + str(error)
                ) from error
            except apt.cache.FetchCancelledException:
                emit("cancelled", message="Download cancelled. No package changes were applied.")
                return False
            finally:
                emit("cancellable", enabled=False)
            if options.get("download_only"):
                emit("notice", message="Downloads complete. No packages were installed or removed.")
                return True
            with InstallProgress(
                emit,
                [change["name"] for change in changes],
                ask=question if options.get("conffile") == "ask" else None,
            ) as install_progress:
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
