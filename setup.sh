#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORBIT_PYTHON="${ORBIT_PYTHON:-/usr/bin/python3}"
"$ORBIT_PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Orbit requires Python 3.11 or newer.")
import apt
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 12):
    raise SystemExit("Orbit requires GTK 4.12 or newer.")
if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
    raise SystemExit("Orbit requires libadwaita 1.5 or newer.")
PY
if [ ! -f "$SCRIPT_DIR/.venv/pyvenv.cfg" ]; then
    uv venv --python "$ORBIT_PYTHON" --system-site-packages "$SCRIPT_DIR/.venv"
fi
uv --project "$SCRIPT_DIR" sync
uv --project "$SCRIPT_DIR" run python -c 'import apt, gi, orbit_gtk'
echo 'Setup complete. Run: uv run orbit-gtk'
