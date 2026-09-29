"""Review and install a local archive through python-apt's DebPackage interface."""

import os
import pwd
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import apt
import apt.debfile
import apt_pkg

from orbit_gtk.backend.progress import CacheProgress, DownloadProgress, InstallProgress
from orbit_gtk.backend.transactions import TransactionRejected, configure_network, describe_changes


@contextmanager
def staged_archive(filename):
    """Open as the requesting user, then review/install a private immutable copy."""
    if Path(filename).suffix != ".deb":
        raise TransactionRejected("Choose a local .deb archive.")
    uid = int(os.environ.get("PKEXEC_UID", os.getuid()))
    previous_uid, previous_gid, groups = os.geteuid(), os.getegid(), os.getgroups()
    try:
        if previous_uid == 0 and uid != 0:
            user = pwd.getpwuid(uid)
            os.setgroups(os.getgrouplist(user.pw_name, user.pw_gid))
            os.setegid(user.pw_gid)
            os.seteuid(uid)
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    finally:
        if previous_uid == 0 and uid != 0:
            os.seteuid(previous_uid)
            os.setegid(previous_gid)
            os.setgroups(groups)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise TransactionRejected("The selected archive must be a regular file.")
        with tempfile.TemporaryDirectory(prefix="orbit-local-") as directory:
            target = Path(directory) / "package.deb"
            with target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            yield str(target)


class LocalProgress(InstallProgress):
    def processing(self, pkg, stage):
        self.emit(
            "progress",
            phase="Installing",
            package=self._package_name(pkg, ""),
            message=stage.capitalize(),
            percent=None,
        )

    def dpkg_status_change(self, pkg, status):
        self.emit(
            "progress",
            phase="Installing",
            package=self._package_name(pkg, ""),
            message={
                "unpacked": "Unpacked",
                "half-configured": "Configuring…",
                "installed": "Installed",
                "half-installed": "Unpacking…",
                "triggers-pending": "Waiting for triggers",
                "triggers-awaited": "Waiting for triggers",
            }.get(status, status),
            percent=None,
        )

    def run(self, filename):
        # DebPackage delegates to this progress object. Use APT's configured dpkg
        # path and conffile policy, rather than PATH lookup in its base fallback.
        class Installer:
            def do_install(self, descriptor):
                os.environ["DPKG_FRONTEND_LOCKED"] = "1"
                os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
                executable = apt_pkg.config.find_file("Dir::Bin::dpkg") or "/usr/bin/dpkg"
                return os.spawnv(
                    os.P_WAIT,
                    executable,
                    [
                        executable,
                        "--force-confold",
                        "--status-fd",
                        str(descriptor),
                        "--install",
                        filename,
                    ],
                )

        return super().run(Installer())


def install_local(filename, emit, approve):
    configure_network()
    with (
        staged_archive(filename) as staged,
        apt_pkg.SystemLock(),
        apt.Cache(progress=CacheProgress(emit)) as cache,
    ):
        archive = apt.debfile.DebPackage(staged, cache)
        if not archive.check():
            raise TransactionRejected(
                archive._failure_string or "This archive cannot be installed."
            )
        name = archive.pkgname
        version = archive["Version"]
        architecture = archive["Architecture"]
        key = (
            name
            if ":" in name
            else f"{name}:{apt_pkg.config.find('APT::Architecture') if architecture == 'all' else architecture}"
        )
        installed = cache[key].installed if key in cache else None
        if key in cache and cache[key]._pkg.selected_state == apt_pkg.SELSTATE_HOLD:
            raise TransactionRejected(f"{name} is held by APT.")
        changes = describe_changes(cache, "install-local")
        if any(change["name"] == key for change in changes):
            raise TransactionRejected(
                "Dependency resolution would also change the local package. Resolve this conflict first."
            )
        local_change = {
            "name": key,
            "action": "reinstall"
            if installed and installed.version == version
            else "upgrade"
            if installed
            else "install",
            "old_version": installed.version if installed else None,
            "new_version": version,
        }
        emit(
            "notice",
            message=f"Local archive: {Path(filename).name}. Only install files from a source you trust; repository signatures do not authenticate this file.",
        )
        try:
            local_size = int(archive["Installed-Size"]) * 1024
        except KeyError:
            local_size = 0
        if not approve(
            {
                "changes": [*changes, local_change],
                "download_bytes": cache.required_download,
                "disk_bytes": cache.required_space
                + local_size
                - (installed.installed_size if installed else 0),
                "kept_back": [],
                "local_archive": Path(filename).name,
            }
        ):
            emit("cancelled", message="Cancelled before applying package changes.")
            return False
        if changes:
            downloads = {
                uri: pkg.fullname
                for pkg in (cache[change["name"]] for change in changes)
                if not pkg.marked_delete and pkg.candidate
                for uri in pkg.candidate.uris
            }
            with InstallProgress(emit, [item["name"] for item in changes]) as progress:
                try:
                    if not cache.commit(
                        DownloadProgress(emit, downloads), progress, allow_unauthenticated=False
                    ):
                        raise RuntimeError("Dependency installation did not complete.")
                except apt.cache.FetchFailedException as error:
                    raise RuntimeError(
                        "Dependency download failed. Check your connection and retry.\n"
                        + str(error)
                    ) from error
        # Retain the frontend lock while allowing dpkg to acquire its database lock.
        apt_pkg.pkgsystem_unlock_inner()
        try:
            with LocalProgress(emit, [key]) as progress:
                if archive.install(progress) != 0:
                    raise RuntimeError(
                        "Local installation did not complete. Dependencies may already have changed; inspect package health on Home."
                    )
        finally:
            apt_pkg.pkgsystem_lock_inner()
    return True
