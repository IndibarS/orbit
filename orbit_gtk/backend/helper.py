"""Privileged process boundary. Package logic lives in imported backend modules."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from orbit_gtk.backend.mirrors import (
    ORBIT_SOURCES_PATH,
    existing_mirror_urls,
    normalize_mirror_url,
    render_orbit_source,
    source_settings,
    validate_suite,
)


def approve(plan: dict, emit) -> bool:
    emit("plan", **plan)
    # EOF, malformed input and an abandoned UI all fail closed.
    ready, _, _ = select.select([sys.stdin], [], [], 300)
    return bool(ready and sys.stdin.readline(128).strip() == "apply")


def clean(targets: Sequence[str], emit) -> None:
    import apt_pkg

    selected = set(targets) or {"apt_cache"}
    if selected - {"apt_cache", "apt_lists"}:
        raise ValueError("Unknown cleanup category.")
    with ExitStack() as stack:
        directories = []
        for key, directory in (
            ("apt_cache", "/var/cache/apt/archives"),
            ("apt_lists", "/var/lib/apt/lists"),
        ):
            if key not in selected:
                continue
            path = Path(directory)
            lock = apt_pkg.get_lock(str(path / "lock"))
            if lock < 0:
                raise RuntimeError(f"Another package manager is using {directory}.")
            stack.callback(os.close, lock)
            directories.append((key, path))
        files = []
        for key, directory in directories:
            files.extend(
                p
                for p in directory.iterdir()
                if p.is_file()
                and not p.is_symlink()
                and p.name != "lock"
                and (key == "apt_lists" or p.suffix == ".deb")
            )
        for index, path in enumerate(files):
            path.unlink(missing_ok=True)
            emit(
                "progress",
                phase="Cleaning",
                message=path.name,
                percent=(index + 1) / len(files) * 100,
            )


def _write_atomically(path: Path, content: str) -> None:
    """Replace an Orbit-owned source file atomically, retaining one rollback copy."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def configure_mirrors(urls: Sequence[str] | None, suite: str | None, emit) -> None:
    path = ORBIT_SOURCES_PATH
    if urls is None:
        path.unlink(missing_ok=True)
        emit("notice", message="Orbit mirrors cleared. Refresh package lists when ready.")
        return
    normalized = list(dict.fromkeys(normalize_mirror_url(url) for url in urls))
    if not normalized:
        raise ValueError("Select at least one mirror.")
    settings = replace(source_settings(), suite=validate_suite(suite or ""))
    existing = existing_mirror_urls(settings.suite)
    selected = [url for url in normalized if url not in existing]
    if selected:
        _write_atomically(path, render_orbit_source(selected, settings))
    else:
        # All selected mirrors already exist elsewhere; an old Orbit selection
        # must not remain active when the user has chosen a different set.
        path.unlink(missing_ok=True)
    skipped = len(normalized) - len(selected)
    emit(
        "notice",
        message=f"Saved {len(selected)} Orbit mirrors; {skipped} already configured. "
        "Refresh package lists when ready. No indexes were downloaded.",
    )


def repair(emit) -> None:
    # dpkg exposes no Python configuration API. Only this recovery action needs
    # an explicit executable; regular operations use python-apt exclusively.
    emit("progress", phase="Repairing", message="Configuring interrupted packages…", percent=None)
    with subprocess.Popen(
        ["/usr/bin/dpkg", "--status-fd=1", "--force-confold", "--configure", "-a"],
        stdout=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    ) as process:
        for line in process.stdout:
            if line.startswith(("processing:", "status:")):
                emit("progress", phase="Repairing", message=line.strip(), percent=None)
            else:
                print(line, end="", file=sys.stderr)
        if process.wait():
            raise RuntimeError(
                "Package configuration could not be repaired. See technical details."
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Orbit privileged package helper")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("update", "upgrade", "full-upgrade", "autoremove", "repair", "clear-mirrors"):
        commands.add_parser(name)
    commands.add_parser("clean").add_argument("targets", nargs="*")
    for name in ("install", "remove", "purge", "reinstall"):
        commands.add_parser(name).add_argument("packages", nargs="+")
    mirrors = commands.add_parser("set-mirrors")
    mirrors.add_argument("--suite", required=True)
    mirrors.add_argument("--urls", nargs="+", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Reserve stdout solely for events, including across APT's fork/exec.
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    native_log = tempfile.TemporaryFile()
    os.dup2(native_log.fileno(), sys.stdout.fileno())
    os.dup2(native_log.fileno(), sys.stderr.fileno())
    log_offset = 0

    def emit(kind: str, **payload) -> None:
        nonlocal log_offset
        try:
            # Native APT/dpkg output writes to an unlinked file, so a closed GUI
            # pipe cannot send SIGPIPE to dpkg halfway through installation.
            sys.stdout.flush()
            sys.stderr.flush()
            size = os.fstat(native_log.fileno()).st_size
            if size > log_offset:
                start = max(log_offset, size - 65536)
                text = os.pread(native_log.fileno(), size - start, start).decode("utf-8", "replace")
                log_offset = size
                channel.write(json.dumps({"event": "log", "message": text}) + "\n")
            channel.write(json.dumps({"event": kind, **payload}, ensure_ascii=True) + "\n")
        except BrokenPipeError:
            # Never abort dpkg halfway through just because the GUI disappeared.
            pass

    try:
        if os.geteuid() != 0:
            raise PermissionError("Package changes require administrator authentication.")
        import apt_pkg

        from orbit_gtk.backend.transactions import execute, update

        os.environ.update(DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="a")
        apt_pkg.init()
        apt_pkg.config.set("Dpkg::Options::", "--force-confold")
        apt_pkg.config.set("Dpkg::Use-Pty", "0")
        apt_pkg.config.set("APT::Get::AllowUnauthenticated", "false")
        # Also serialize mirror/cleanup/recovery operations across Orbit windows.
        descriptor = os.open(
            "/run/lock/orbit-gtk.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command in {
                "install",
                "remove",
                "purge",
                "reinstall",
                "upgrade",
                "full-upgrade",
                "autoremove",
            }:
                if not execute(
                    args.command,
                    getattr(args, "packages", []),
                    emit,
                    lambda plan: approve(plan, emit),
                ):
                    return 2
            elif args.command == "update":
                update(emit)
            elif args.command == "clean":
                clean(args.targets, emit)
            elif args.command == "repair":
                repair(emit)
            elif args.command == "set-mirrors":
                configure_mirrors(args.urls, args.suite, emit)
            else:
                configure_mirrors(None, None, emit)
        emit("complete", message="Operation completed successfully.")
        return 0
    except Exception as error:
        emit("error", message=str(error) or type(error).__name__)
        return 1
    finally:
        try:
            channel.close()
        except BrokenPipeError:
            pass
        native_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
