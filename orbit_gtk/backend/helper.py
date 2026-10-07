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
import threading
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from orbit_gtk.backend.mirror_catalogues import validate_mirror
from orbit_gtk.backend.mirrors import (
    ORBIT_SOURCES_PATH,
    existing_mirror_urls,
    normalize_mirror_url,
    replace_profile,
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


def configure_mirrors(
    urls: Sequence[str] | None,
    suite: str | None,
    emit,
    repository=None,
    *,
    override_suite=None,
    components=None,
    sources=False,
) -> None:
    path = ORBIT_SOURCES_PATH
    if urls is None and repository is None:
        path.unlink(missing_ok=True)
        emit("notice", message="Orbit mirrors cleared. Refresh package lists when ready.")
        return
    settings = source_settings(repository) if repository else source_settings()
    if urls is not None and suite != settings.suite:
        raise ValueError("Repository settings changed. Reload and benchmark again.")
    if override_suite:
        settings = replace(settings, suite=validate_suite(override_suite))
    if components:
        settings = replace(settings, components=tuple(validate_suite(c) for c in components))
    settings = replace(settings, source_packages=sources)
    normalized = list(dict.fromkeys(normalize_mirror_url(url) for url in (urls or [])))
    if urls is not None and not 1 <= len(normalized) <= 16:
        raise ValueError("Select between 1 and 16 mirrors.")
    # No source write until every selected archive passes the same checks as the GUI.
    for url in normalized:
        emit("progress", phase="Checking mirrors", message=url)
        validate_mirror(url, settings)
    existing = existing_mirror_urls(settings.suite)
    selected = [
        url for url in normalized if url not in existing or sources or components or override_suite
    ]
    try:
        previous = path.read_text()
    except FileNotFoundError:
        previous = ""
    content = replace_profile(previous, selected, settings)
    if content:
        _write_atomically(path, content)
    else:
        path.unlink(missing_ok=True)
    emit(
        "notice",
        message=f"Saved {len(selected)} mirrors for {settings.label}; "
        f"{len(normalized) - len(selected)} already configured. "
        "Other repositories are unchanged. Refresh package lists when ready.",
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
    for name in (
        "update",
        "upgrade",
        "full-upgrade",
        "autoremove",
        "autopurge",
        "purge-config",
        "fix-broken",
        "batch",
        "repair",
        "clear-mirrors",
    ):
        command_parser = commands.add_parser(name)
        command_parser.add_argument("--options", default="{}")
        if name == "clear-mirrors":
            command_parser.add_argument("--repository")
    commands.add_parser("clean").add_argument("targets", nargs="*")
    for name in ("install", "remove", "purge", "reinstall"):
        command_parser = commands.add_parser(name)
        command_parser.add_argument("packages", nargs="+")
        command_parser.add_argument("--options", default="{}")
    commands.add_parser("install-local").add_argument("path")
    local = commands.add_parser("install-batch")
    local.add_argument("--paths", nargs="+", required=True)
    local.add_argument("--packages", nargs="*", default=[])
    mirrors = commands.add_parser("set-mirrors")
    mirrors.add_argument("--repository")
    mirrors.add_argument("--override-suite")
    mirrors.add_argument("--components", nargs="+")
    mirrors.add_argument("--sources", action="store_true")
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
    journal = None

    def emit(kind: str, **payload) -> None:
        nonlocal log_offset
        if journal is not None:
            try:
                journal.event(kind, payload)
            except OSError:
                if kind == "plan":
                    raise  # Do not apply an operation whose review cannot be recorded.
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

    question_lock = threading.Lock()

    def question(payload):
        with question_lock:
            emit("question", **payload)
            ready, _, _ = select.select([sys.stdin], [], [], 300)
            if ready:
                try:
                    reply = json.loads(sys.stdin.readline(65536))
                    if isinstance(reply, dict) and isinstance(reply.get("answer"), str):
                        return reply["answer"]
                except ValueError:
                    pass
            emit(
                "warning",
                message="Configuration question timed out or the window closed; keeping its default.",
            )
            return payload.get("default", "")

    def cancelled():
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(ready and os.read(sys.stdin.fileno(), 128).strip() == b"cancel")

    try:
        if os.geteuid() != 0:
            raise PermissionError("Package changes require administrator authentication.")
        import apt_pkg

        from orbit_gtk.backend.transaction_options import validate_options
        from orbit_gtk.backend.transactions import execute, update

        options = validate_options(json.loads(getattr(args, "options", "{}")))
        os.environ.update(DEBIAN_FRONTEND="noninteractive", NEEDRESTART_MODE="a")
        apt_pkg.init()
        if options.get("conffile") != "ask":
            apt_pkg.config.set(
                "Dpkg::Options::",
                "--force-confnew" if options.get("conffile") == "replace" else "--force-confold",
            )
        apt_pkg.config.set("Dpkg::Use-Pty", "0")
        apt_pkg.config.set("APT::Get::AllowUnauthenticated", "false")
        # Also serialize mirror/cleanup/recovery operations across Orbit windows.
        descriptor = os.open(
            "/run/lock/orbit-gtk.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        with ExitStack() as operation_scope, os.fdopen(descriptor, "w") as lock:
            if options.get("interactive"):
                from orbit_gtk.backend.debconf import DebconfBridge

                operation_scope.enter_context(DebconfBridge(question))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            from orbit_gtk.backend.journal import Journal

            journal = Journal(
                args.command,
                Path(apt_pkg.config.find_dir("Dir::State")).parent / "orbit-gtk" / "history",
            )
            if args.command in {
                "install",
                "remove",
                "purge",
                "reinstall",
                "upgrade",
                "full-upgrade",
                "autoremove",
                "autopurge",
                "purge-config",
                "fix-broken",
                "batch",
            }:
                if not execute(
                    args.command,
                    getattr(args, "packages", []),
                    emit,
                    lambda plan: approve(plan, emit),
                    options=options,
                    cancelled=cancelled,
                    question=question,
                ):
                    return 2
            elif args.command == "install-batch":
                from orbit_gtk.backend.local_batch import install_batch

                if not install_batch(
                    args.paths, args.packages, emit, lambda plan: approve(plan, emit), cancelled
                ):
                    return 2
            elif args.command == "install-local":
                from orbit_gtk.backend.local_deb import install_local

                if not install_local(args.path, emit, lambda plan: approve(plan, emit)):
                    return 2
            elif args.command == "update":
                update(emit)
            elif args.command == "clean":
                clean(args.targets, emit)
            elif args.command == "repair":
                repair(emit)
            elif args.command == "set-mirrors":
                configure_mirrors(
                    args.urls,
                    args.suite,
                    emit,
                    args.repository,
                    override_suite=args.override_suite,
                    components=args.components,
                    sources=args.sources,
                )
            else:
                configure_mirrors(None, None, emit, args.repository)
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
