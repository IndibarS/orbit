"""Run inside a disposable Docker container, never against the host package root."""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

identity = platform.freedesktop_os_release()
versions = {
    "os": identity,
    "python": sys.version,
    "gtk": [Gtk.get_major_version(), Gtk.get_minor_version()],
    "adwaita": [Adw.get_major_version(), Adw.get_minor_version()],
}
print("RUNTIME " + json.dumps(versions), flush=True)
if versions["gtk"] < [4, 12] or versions["adwaita"] < [1, 5] or sys.version_info < (3, 11):
    raise SystemExit("UNSUPPORTED: requires Python 3.11, GTK 4.12 and libadwaita 1.5")

if not Path("/.dockerenv").exists():
    raise SystemExit("This test runner must run inside Docker.")

expected = os.environ.get("ORBIT_EXPECTED_DISTRO")
if not expected or identity.get("ID") != expected:
    raise SystemExit(f"Wrong distro fixture: expected {expected!r}, got {identity.get('ID')!r}")

from orbit_gtk.backend.mirrors import source_settings  # noqa: E402
from orbit_gtk.backend.system_identity import system_identity  # noqa: E402

app_identity = system_identity()
print("IDENTITY " + json.dumps(app_identity), flush=True)
if expected == "debian":
    suite = source_settings().suite
    expected_label = "Sid (unstable)" if suite in {"sid", "unstable"} else suite
    if app_identity["suite"] != expected_label:
        raise SystemExit(f"Incorrect Debian suite label: {app_identity!r}")
elif app_identity["distro"] != identity["PRETTY_NAME"]:
    raise SystemExit(f"Derivative identity lost: {app_identity!r}")

failures = []


def check(name, command, **kwargs):
    print(f"CHECK {name}", flush=True)
    result = subprocess.run(command, **kwargs)
    if result.returncode:
        failures.append(name)
    print(f"RESULT {name}: {result.returncode}", flush=True)


check(
    "backend",
    ["uv", "run", "--offline", "python", "-m", "unittest", "discover", "-s", "tests", "-v"],
)
check(
    "gtk",
    [
        "dbus-run-session",
        "--",
        "xvfb-run",
        "-a",
        "uv",
        "run",
        "--offline",
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_gui.py",
        "-v",
    ],
    env={**os.environ, "ORBIT_GUI_TESTS": "1"},
)
check(
    "cli-ipc",
    [
        "dbus-run-session",
        "--",
        "xvfb-run",
        "-a",
        "uv",
        "run",
        "--offline",
        "python",
        "tests/isolated_cli.py",
    ],
)
check(
    "network",
    [
        "uv",
        "run",
        "--offline",
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_network.py",
        "-v",
    ],
    env={**os.environ, "ORBIT_NETWORK_TESTS": "1"},
)
check(
    "transactions",
    ["uv", "run", "--offline", "python", "tests/isolated_transaction.py"],
    env={**os.environ, "ORBIT_ISOLATED_ROOT": "1"},
)
check("build", ["uv", "run", "--offline", "python", "tools/build_deb.py"])
archives = sorted(Path("dist").glob("orbit-gtk_*.deb"))
if archives:
    check(
        "install-deb",
        ["apt-get", "install", "-y", "--no-install-recommends", str(archives[-1].resolve())],
    )
    check("installed-cli", ["/usr/bin/orbit-gtk", "--help"])
else:
    failures.append("missing-deb")
    print("RESULT missing-deb: no built archive found", flush=True)
print("SUMMARY " + json.dumps({"os": identity.get("PRETTY_NAME"), "failed": failures}), flush=True)
raise SystemExit(bool(failures))
