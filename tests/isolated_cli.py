"""Run under xvfb-run and dbus-run-session; intercept mutations, test real IPC."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    log = Path(directory) / "requests"
    service = Path(directory) / "service.py"
    service.write_text(
        """import sys, json
sys.path.insert(0, sys.argv.pop(1))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from orbit_gtk.ui.window import OrbitWindow
from orbit_gtk.main import main
from gi.repository import GLib
original = OrbitWindow.dispatch_command
def record(self, request):
    with open("""
        + repr(str(log))
        + """, "a") as output:
        output.write(json.dumps(vars(request))+"\\n")
    if request.command == "history":
        GLib.timeout_add(300, lambda: self.get_application().quit())
    elif request.command in ("list", "search"):
        original(self, request)
OrbitWindow.dispatch_command = record
raise SystemExit(main())
"""
    )
    process = subprocess.Popen([sys.executable, str(service), str(root), "list", "--installed"])
    try:
        deadline = time.monotonic() + 45
        while not log.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.1)
        assert log.exists(), f"Initial startup failed: {process.poll()}"
        for arguments in (
            ["install", "./some package.deb"],
            ["search", "text", "editor"],
            ["history"],
        ):
            subprocess.run(
                [sys.executable, "-m", "orbit_gtk.main", *arguments],
                cwd=directory,
                env={**os.environ, "PYTHONPATH": str(root)},
                check=True,
                timeout=45,
            )
        process.wait(timeout=45)
        requests = [json.loads(line) for line in log.read_text().splitlines()]
        assert requests[0]["installed"]
        assert requests[1]["path"] == str(Path(directory) / "some package.deb"), requests
        assert [item["command"] for item in requests] == [
            "list",
            "install-local",
            "search",
            "history",
        ], requests
        print(
            "PASS: real GApplication startup, option parsing, single-instance forwarding and caller-relative archive paths"
        )
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
