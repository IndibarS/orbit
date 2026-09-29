"""Real APT/dpkg lifecycle in a temporary root, never the host package database.

Run with the system Python or `uv run python tests/isolated_transaction.py`.
Bubblewrap creates an isolated user namespace with a read-only host root.
No root privileges or downloads are needed. The dpkg wrapper unconditionally
prepends --root and --force-not-root to every dpkg invocation.
"""

import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import apt
import apt_pkg

from orbit_gtk.backend.transactions import execute, update


def run():
    if os.environ.get("ORBIT_ISOLATED_ROOT") != "1":
        return subprocess.run(
            [
                "bwrap",
                "--unshare-all",
                "--die-with-parent",
                "--uid",
                "0",
                "--gid",
                "0",
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/etc/dpkg",
                "--setenv",
                "ORBIT_ISOLATED_ROOT",
                "1",
                sys.executable,
                str(Path(__file__).resolve()),
            ],
            check=True,
        ).returncode
    with tempfile.TemporaryDirectory(prefix="orbit-integration-") as directory:
        base = Path(directory)
        root = base / "root"
        repo = base / "repo"
        repo.mkdir()
        for version in ("1.0", "2.0"):
            package = base / f"package-{version}"
            (package / "DEBIAN").mkdir(parents=True)
            (package / "usr/share/orbit-test").mkdir(parents=True)
            (package / "usr/share/orbit-test/version").write_text(version)
            (package / "etc").mkdir()
            (package / "etc/orbit-fixture.conf").write_text("test configuration\n")
            (package / "DEBIAN/conffiles").write_text("/etc/orbit-fixture.conf\n")
            (package / "DEBIAN/control").write_text(
                f"Package: orbit-integration-fixture\nVersion: {version}\nArchitecture: all\n"
                "Maintainer: Orbit Tests <test@example.invalid>\nDescription: Disposable integration fixture\n"
            )
            subprocess.run(
                [
                    "dpkg-deb",
                    "--build",
                    "--root-owner-group",
                    str(package),
                    str(repo / f"fixture-{version}.deb"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        wrapper = base / "dpkg-isolated"
        wrapper.write_text(
            f"#!{sys.executable}\nimport os,sys\n"
            f"os.execv('/usr/bin/dpkg', ['dpkg', '--root={root}', '--force-not-root', '--log={root}/var/log/dpkg.log', *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        # apt.Cache(rootdir=...) configures APT paths and creates its temp layout.
        cache = apt.Cache(rootdir=str(root))
        cache.close()
        (root / "var/log/apt").mkdir(parents=True, exist_ok=True)
        (root / "var/lib/dpkg/updates").mkdir(parents=True, exist_ok=True)
        (root / "var/lib/dpkg/info").mkdir(parents=True, exist_ok=True)
        (root / "etc/apt/sources.list").write_text(f"deb [trusted=yes] file:{repo} ./\n")
        apt_pkg.config.set("Dir::Bin::dpkg", str(wrapper))
        apt_pkg.config.set("APT::Sandbox::User", "root")
        apt_pkg.config.set("Dpkg::Use-Pty", "0")
        apt_pkg.config.set("DPkg::Options::", "--force-confold")
        # No host hooks or global sources are allowed into the test.
        for key in (
            "DPkg::Pre-Invoke",
            "DPkg::Post-Invoke",
            "DPkg::Pre-Install-Pkgs",
            "APT::Update::Post-Invoke",
            "APT::Update::Post-Invoke-Success",
        ):
            apt_pkg.config.clear(key)
        events = []
        plans = []

        def emit(kind, **payload):
            events.append((kind, payload))

        def approve(plan):
            plans.append(plan)
            return True

        def publish(version):
            archive = repo / f"fixture-{version}.deb"
            result = subprocess.run(
                ["dpkg-scanpackages", str(archive), "/dev/null"],
                check=True,
                capture_output=True,
                text=True,
            )
            (repo / "Packages").write_text(result.stdout.replace(str(repo) + "/", ""))
            stamp = time.time() + int(version[0]) * 10
            os.utime(repo / "Packages", (stamp, stamp))
            update(emit)

        publish("1.0")
        assert execute("install", ["orbit-integration-fixture"], emit, approve)
        installed = root / "usr/share/orbit-test/version"
        assert installed.read_text() == "1.0"
        publish("2.0")
        assert execute("upgrade", [], emit, approve)
        assert installed.read_text() == "2.0"
        assert execute("remove", ["orbit-integration-fixture"], emit, approve)
        assert not installed.exists()
        assert [p["changes"][0]["action"] for p in plans] == ["install", "upgrade", "remove"]
        configuration = root / "etc/orbit-fixture.conf"
        assert configuration.exists(), "Normal removal must retain package configuration"
        assert execute("install", ["orbit-integration-fixture"], emit, approve)
        assert execute("purge", ["orbit-integration-fixture"], emit, approve)
        assert not configuration.exists(), "Purge must remove package configuration"
        assert plans[-1]["changes"][0]["action"] == "purge"
        assert execute("install", ["orbit-integration-fixture"], emit, approve)
        installed.write_text("damaged fixture")
        assert not execute("reinstall", ["orbit-integration-fixture"], emit, lambda _: False)
        assert installed.read_text() == "damaged fixture", "Cancellation must leave files unchanged"
        assert execute("reinstall", ["orbit-integration-fixture"], emit, approve)
        assert installed.read_text() == "2.0", "Reinstall must restore package files"
        assert plans[-1]["changes"][0]["action"] == "reinstall"
        # Mark only the disposable fixture automatic in the isolated database.
        (root / "var/lib/apt/extended_states").write_text(
            "Package: orbit-integration-fixture\nArchitecture: amd64\nAuto-Installed: 1\n\n"
        )
        with apt.Cache() as check:
            assert check["orbit-integration-fixture"].is_auto_removable
        assert not execute("autoremove", [], emit, lambda _: False)
        assert installed.exists(), "Cancelled autoremove must retain the package"
        assert execute("autoremove", [], emit, approve)
        assert not installed.exists()
        assert configuration.exists(), "Autoremove must retain configuration"
        statuses = [
            p["message"]
            for kind, p in events
            if kind == "progress" and p.get("phase") == "Installing"
        ]
        assert any("Unpacking" in status for status in statuses), statuses
        assert any("Configuring" in status for status in statuses), statuses
        assert any(
            kind == "package-progress" and payload["package"] == "orbit-integration-fixture:amd64"
            for kind, payload in events
        ), events
        # Exercise the real privileged helper's review and JSON framing too.
        Path("/run/lock").mkdir(parents=True, exist_ok=True)
        config = base / "apt.conf"
        config.write_text(apt_pkg.config.dump())
        environment = dict(os.environ, APT_CONFIG=str(config))

        def helper(action, answer, target="orbit-integration-fixture"):
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "orbit_gtk.backend.helper",
                    action,
                    target,
                ],
                input=answer + "\n",
                capture_output=True,
                text=True,
                env=environment,
                timeout=30,
            )
            parsed = [json.loads(line) for line in process.stdout.splitlines()]
            assert any(event["event"] == "plan" for event in parsed), (parsed, process.stderr)
            return process, parsed

        cancelled, cancel_events = helper("install", "cancel")
        assert cancelled.returncode == 2, (cancelled.stdout, cancelled.stderr)
        assert not installed.exists()
        applied, apply_events = helper("install", "apply")
        assert applied.returncode == 0, (applied.stdout, applied.stderr)
        assert installed.read_text() == "2.0"
        assert apply_events[-1]["event"] == "complete", apply_events
        assert any(event["event"] == "log" for event in apply_events), apply_events
        removed, _ = helper("remove", "apply")
        assert removed.returncode == 0
        assert not installed.exists()
        print("PASS: real helper review cancellation, approved install, logs and removal")

        def disconnected_gui(approved):
            with subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "orbit_gtk.backend.helper",
                    "install",
                    "orbit-integration-fixture",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=environment,
            ) as process:
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        ready, _, _ = select.select(
                            [process.stdout], [], [], max(0, deadline - time.monotonic())
                        )
                        assert ready, "Helper did not produce a review plan"
                        line = process.stdout.readline()
                        assert line, "Helper exited before review"
                        if json.loads(line)["event"] == "plan":
                            break
                    if approved:
                        process.stdin.write(b"apply\n")
                        process.stdin.flush()
                    process.stdin.close()
                    process.stdout.close()
                    return process.wait(timeout=30)
                finally:
                    if process.poll() is None:
                        process.kill()  # Only the disposable fixture namespace.

        assert disconnected_gui(False) == 2
        assert not installed.exists(), "Lost GUI before approval must not install anything"
        assert disconnected_gui(True) == 0
        assert installed.read_text() == "2.0", (
            "Approved operation must finish despite lost GUI pipes"
        )
        print(
            "PASS: lost GUI cancels before approval and cannot abort an approved dpkg transaction"
        )
        # Add an unreachable HTTP source inside the network-isolated namespace.
        # Failed refresh must not discard the already usable cached catalogue.
        sources = root / "etc/apt/sources.list"
        original_sources = sources.read_text()
        sources.write_text(
            original_sources + "deb [trusted=yes] http://127.0.0.1:9/unreachable ./\n"
        )
        apt_pkg.config.set("Acquire::Retries", "0")
        apt_pkg.config.set("Acquire::http::Timeout", "1")
        try:
            update(emit)
        except RuntimeError as error:
            assert "refresh failed" in str(error).lower(), str(error)
        else:
            raise AssertionError("An unreachable repository must not report a successful refresh")
        with apt.Cache() as cached:
            assert cached["orbit-integration-fixture"].candidate.version == "2.0"
            assert cached["orbit-integration-fixture"].is_installed
        sources.write_text(original_sources)
        # Remove the archive after APT has indexed it: an interrupted/unavailable
        # package download must not reach dpkg. Restore it and verify retry.
        archive = repo / "fixture-2.0.deb"
        missing = repo / "fixture-offline.deb"
        archive.rename(missing)
        before = installed.read_text()
        failed_events = []
        try:
            execute(
                "reinstall",
                ["orbit-integration-fixture"],
                lambda kind, **payload: failed_events.append((kind, payload)),
                approve,
            )
        except RuntimeError as error:
            assert "download failed" in str(error).lower(), str(error)
        else:
            raise AssertionError("Missing archive must fail before installation")
        assert installed.read_text() == before
        assert not any(payload.get("phase") == "Installing" for _, payload in failed_events)
        missing.rename(archive)
        assert execute("reinstall", ["orbit-integration-fixture"], emit, approve)
        print(
            "PASS: offline repository failure preserves cached data; failed download leaves files unchanged and retry succeeds"
        )
        print(
            f"PASS: isolated refresh, install, upgrade and removal; {len(events)} callback events"
        )
        # A dependency transition that requires removal: normal upgrade keeps
        # it back, full upgrade must review the exact removal before committing.
        transition_repo = base / "transition-repo"
        transition_repo.mkdir()
        for name, version, relations in (
            ("orbit-old-library", "1", ""),
            ("orbit-new-library", "1", "Conflicts: orbit-old-library\n"),
            ("orbit-transition", "1", "Depends: orbit-old-library\n"),
            ("orbit-transition", "2", "Depends: orbit-new-library\n"),
        ):
            directory = base / f"{name}-{version}"
            (directory / "DEBIAN").mkdir(parents=True)
            (directory / "DEBIAN/control").write_text(
                f"Package: {name}\nVersion: {version}\nArchitecture: all\n{relations}"
                "Maintainer: Orbit Tests <test@example.invalid>\nDescription: Transition fixture\n"
            )
            subprocess.run(
                [
                    "dpkg-deb",
                    "--build",
                    "--root-owner-group",
                    str(directory),
                    str(transition_repo / f"{name}-{version}.deb"),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        second = transition_repo / "orbit-transition-2.deb"
        hidden = base / "transition-2.deb"
        second.rename(hidden)

        def publish_transition(stamp):
            result = subprocess.run(
                ["dpkg-scanpackages", str(transition_repo), "/dev/null"],
                check=True,
                capture_output=True,
                text=True,
            )
            index = transition_repo / "Packages"
            index.write_text(result.stdout.replace(str(transition_repo) + "/", ""))
            os.utime(index, (stamp, stamp))
            update(emit)

        sources.write_text(original_sources + f"deb [trusted=yes] file:{transition_repo} ./\n")
        publish_transition(time.time() + 100)
        assert execute("install", ["orbit-transition"], emit, approve)
        hidden.rename(second)
        publish_transition(time.time() + 200)
        assert execute("upgrade", [], emit, approve)
        with apt.Cache() as cached:
            assert cached["orbit-transition"].installed.version == "1"
        reviewed = []
        assert not execute("full-upgrade", [], emit, lambda plan: reviewed.append(plan) or False)
        changes = {(item["name"].split(":")[0], item["action"]) for item in reviewed[0]["changes"]}
        assert ("orbit-old-library", "remove") in changes, changes
        assert ("orbit-new-library", "install") in changes, changes
        with apt.Cache() as cached:
            assert cached["orbit-old-library"].is_installed
            assert cached["orbit-transition"].installed.version == "1"
        assert execute("full-upgrade", [], emit, approve)
        with apt.Cache() as cached:
            assert not cached["orbit-old-library"].is_installed
            assert cached["orbit-new-library"].is_installed
            assert cached["orbit-transition"].installed.version == "2"

        # Leave a real package unpacked, inspect it through the application's
        # health reader, then configure it using the isolated dpkg wrapper.
        from orbit_gtk.backend.apt_cache import OrbitAptCache

        dpkg_env = {**os.environ, "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
        subprocess.run([str(wrapper), "--unpack", str(second)], check=True, env=dpkg_env)
        with apt.Cache() as cached:
            view = OrbitAptCache()
            view._cache, view._initialized = cached, True
            assert any(
                name.startswith("orbit-transition")
                for name in view.get_health().pending_configuration
            )
        subprocess.run([str(wrapper), "--configure", "-a"], check=True, env=dpkg_env)
        with apt.Cache() as cached:
            view._cache = cached
            assert not view.get_health().needs_attention
        print(
            "PASS: full-upgrade dependency removal review, cancellation, commit and real unfinished-package detection"
        )
        unpack_events = [
            payload
            for kind, payload in events
            if kind == "progress"
            and payload.get("message", "").startswith("Unpacking orbit-integration-fixture")
        ]
        assert unpack_events and all(
            payload["package"] == "orbit-integration-fixture:amd64" for payload in unpack_events
        ), unpack_events
        print(
            "PASS: real installation stages address the same architecture-qualified rows as downloads"
        )
        from orbit_gtk.backend.local_deb import install_local

        # Exercise an archive absent from the repository index, with a repository
        # dependency, without giving the host dpkg database write access.
        dependency = base / "local-dependency"
        (dependency / "DEBIAN").mkdir(parents=True)
        (dependency / "DEBIAN/control").write_text(
            "Package: orbit-local-dependency\nVersion: 1\nArchitecture: all\n"
            "Maintainer: Orbit Tests <test@example.invalid>\nDescription: Local dependency fixture\n"
        )
        dependency_archive = transition_repo / "local-dependency.deb"
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(dependency), str(dependency_archive)],
            check=True,
        )
        publish_transition(time.time() + 300)
        directory = base / "local-package"
        (directory / "DEBIAN").mkdir(parents=True)
        (directory / "DEBIAN/control").write_text(
            "Package: orbit-local-fixture\nVersion: 1\nArchitecture: all\n"
            "Depends: orbit-local-dependency\nInstalled-Size: 1\n"
            "Maintainer: Orbit Tests <test@example.invalid>\nDescription: Local fixture\n"
        )
        local_archive = base / "local package.deb"
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(directory), str(local_archive)],
            check=True,
        )
        assert not install_local(str(local_archive), emit, lambda plan: False)
        with apt.Cache() as cached:
            assert (
                "orbit-local-fixture" not in cached
                or not cached["orbit-local-fixture"].is_installed
            )
        with apt.Cache() as cached:
            assert not cached["orbit-local-dependency"].is_installed
        missing_dependency = base / "missing-dependency.deb"
        dependency_archive.rename(missing_dependency)
        try:
            install_local(str(local_archive), emit, approve)
        except RuntimeError as error:
            assert "download failed" in str(error).lower(), error
        else:
            raise AssertionError("Missing dependency archive must prevent local installation")
        missing_dependency.rename(dependency_archive)
        assert install_local(str(local_archive), emit, approve)
        with apt.Cache() as cached:
            assert cached["orbit-local-fixture"].is_installed
            assert cached["orbit-local-dependency"].is_installed
        assert install_local(str(local_archive), emit, approve)
        assert plans[-1]["changes"][-1]["action"] == "reinstall"
        subprocess.run(
            [str(wrapper), "--set-selections"],
            input="orbit-local-fixture hold\n",
            text=True,
            check=True,
            env=dpkg_env,
        )
        try:
            install_local(str(local_archive), emit, approve)
        except ValueError as error:
            assert "held" in str(error), error
        else:
            raise AssertionError("Held local packages must not be reinstalled")
        subprocess.run(
            [str(wrapper), "--set-selections"],
            input="orbit-local-fixture install\n",
            text=True,
            check=True,
            env=dpkg_env,
        )
        control = directory / "DEBIAN/control"
        control.write_text(control.read_text().replace("Version: 1", "Version: 0.5"))
        older_archive = base / "older.deb"
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(directory), str(older_archive)],
            check=True,
        )
        try:
            install_local(str(older_archive), emit, approve)
        except ValueError:
            pass
        else:
            raise AssertionError("A local archive must not silently downgrade installed packages")
        local_events = [
            payload
            for kind, payload in events
            if kind == "progress" and payload.get("package") == "orbit-local-fixture:amd64"
        ]
        assert any(payload["message"] == "Unpacking…" for payload in local_events)
        assert any(payload["message"] == "Installed" for payload in local_events)
        corrupt = base / "corrupt.deb"
        corrupt.write_text("not an archive")
        try:
            install_local(str(corrupt), emit, approve)
        except (SystemError, ValueError, apt_pkg.Error):
            pass
        else:
            raise AssertionError("Corrupt local archives must be rejected")
        print(
            "PASS: local archive review, cancellation, install, reinstall and malformed-archive rejection"
        )
        local_cancelled, local_review = helper("install-local", "cancel", str(local_archive))
        assert local_cancelled.returncode == 2, local_cancelled.stdout
        local_applied, local_result = helper("install-local", "apply", str(local_archive))
        assert local_applied.returncode == 0, (local_applied.stdout, local_applied.stderr)
        assert any(event.get("local_archive") == "local package.deb" for event in local_review)
        assert local_result[-1]["event"] == "complete"
        print("PASS: actual local-install helper protocol, cancellation and approval")
        print("Plans:", [p["changes"] for p in plans])
        print("Installation phases:", sorted(set(statuses)))


if __name__ == "__main__":
    run()
