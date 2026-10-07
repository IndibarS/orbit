"""User-owned pending requests survive navigation and application restarts."""

import json
import os
import tempfile
from pathlib import Path


def selection_path():
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "orbit-gtk/selection.json"
    )


def load_selection(path=None):
    try:
        path = path or selection_path()
        if path.stat().st_size > 1_000_000:
            return {}
        value = json.loads(path.read_text())
        from orbit_gtk.backend.transactions import PACKAGE_NAME

        if isinstance(value, dict) and len(value) <= 10000:
            return {
                k: v
                for k, v in value.items()
                if PACKAGE_NAME.fullmatch(k) and v in {"install", "remove", "purge", "reinstall"}
            }
    except (OSError, ValueError):
        pass
    return {}


def save_selection(value, path=None):
    path = path or selection_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".selection-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
