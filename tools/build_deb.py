"""Build a local Debian artifact without elevating or installing anything."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ID = "io.github.orbit_package_manager.Orbit"


def stage(destination: Path) -> None:
    """Assemble only runtime sources, never the checkout or its dependencies."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    for source in (ROOT / "orbit_gtk").rglob("*.py"):
        target = destination / "usr/lib/orbit-gtk" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    for source in (ROOT / "orbit_gtk/locales").rglob("*.mo"):
        target = destination / "usr/lib/orbit-gtk" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    files = {
        "orbit-helper": ("usr/libexec/orbit-gtk/orbit-helper", 0o755),
        "orbit-gtk": ("usr/bin/orbit-gtk", 0o755),
        f"{APP_ID}.policy": (f"usr/share/polkit-1/actions/{APP_ID}.policy", 0o644),
        f"{APP_ID}.desktop": (f"usr/share/applications/{APP_ID}.desktop", 0o644),
    }
    for source, (relative, mode) in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "packaging" / source, target)
        target.chmod(mode)
    control = destination / "DEBIAN/control"
    control.parent.mkdir(parents=True)
    installed_size = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    control.write_text(
        f"Package: orbit-gtk\nVersion: {version}\nArchitecture: all\n"
        "Maintainer: Orbit local builds <orbit@localhost>\n"
        "Section: admin\nPriority: optional\n"
        f"Installed-Size: {(installed_size + 1023) // 1024}\n"
        "Depends: python3 (>= 3.11), python3-apt, python3-gi, gir1.2-gtk-4.0 (>= 4.12), "
        "gir1.2-adw-1 (>= 1.5), pkexec\n"
        "Recommends: gir1.2-appstream-1.0, apt-config-icons\n"
        "Description: Native GTK package manager for Debian\n"
        " Review APT dependency changes and follow package progress in a native GUI.\n"
    )
    control.chmod(0o644)
    # Enforce modes independent of the builder's umask. dpkg-deb sets ownership.
    for directory in destination.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o755)
    destination.chmod(0o755)


def build(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="orbit-deb-") as temporary:
        root = Path(temporary) / "package"
        stage(root)
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(root), str(output.resolve())],
            check=True,
        )
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    return output / f"orbit-gtk_{version}_all.deb"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    print(build(parser.parse_args().output))
