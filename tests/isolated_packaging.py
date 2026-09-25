"""Execute the shipped launchers in a namespace, with hostile import paths."""

import os
import subprocess
import tempfile
from pathlib import Path

from tools.build_deb import build, stage


def run():
    with tempfile.TemporaryDirectory(prefix="orbit-release-test-") as directory:
        base = Path(directory)
        root = base / "root"
        stage(root)
        poison = base / "poison"
        poison.mkdir()
        (poison / "orbit_gtk").mkdir()
        (poison / "orbit_gtk/__init__.py").write_text("raise RuntimeError('UNTRUSTED IMPORT')\n")
        (poison / "sitecustomize.py").write_text("raise RuntimeError('UNTRUSTED SITE')\n")
        # Bind the shipped paths into a read-only host namespace. Neither the
        # real /usr tree nor the host package database is changed.
        command = [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/run",
            "--overlay-src",
            "/usr",
            "--overlay-src",
            str(root / "usr"),
            "--ro-overlay",
            "/usr",
            "--chdir",
            str(poison),
        ]
        environment = dict(os.environ, PYTHONPATH=str(poison), PYTHONHOME=str(poison))
        helper = subprocess.run(
            [*command, "/usr/libexec/orbit-gtk/orbit-helper", "--help"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert helper.returncode == 0, helper.stderr
        assert "autoremove" in helper.stdout
        assert "UNTRUSTED" not in helper.stderr
        gui = subprocess.run(
            [*command, "/usr/bin/orbit-gtk", "--help"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert gui.returncode == 0, gui.stderr
        assert "Usage:" in gui.stdout
        assert "UNTRUSTED" not in gui.stderr
        print(
            "PASS: shipped helper and GUI launchers ignore hostile cwd, PYTHONPATH and PYTHONHOME"
        )
        archive = build(base / "artifacts")
        install_root = base / "installed"
        install_root.mkdir()
        lifecycle = """
import os, subprocess, sys
from pathlib import Path
archive, root = sys.argv[1], Path(sys.argv[2])
command = ['dpkg', f'--root={root}', '--force-depends', f'--log={root}/dpkg.log']
def run(*args):
    result = subprocess.run([*command, *args], capture_output=True, text=True,
                            env=dict(os.environ, PATH='/usr/sbin:/usr/bin:/sbin:/bin'))
    assert result.returncode == 0, result.stderr
run('--install', archive)
helper = root / 'usr/libexec/orbit-gtk/orbit-helper'
original = helper.read_bytes()
helper.write_text('damaged file')
run('--install', archive)
assert helper.read_bytes() == original
run('--remove', 'orbit-gtk')
assert not helper.exists()
assert not (root / 'usr/lib/orbit-gtk/orbit_gtk/backend/helper.py').exists()
assert not list((root / 'usr/share/polkit-1/actions').glob('*.policy'))
print('PASS: release install, replacement and removal in isolated dpkg root')
"""
        subprocess.run(
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
                "/run",
                "--tmpfs",
                "/etc/dpkg",
                "--bind",
                str(install_root),
                str(install_root),
                "/usr/bin/python3",
                "-I",
                "-c",
                lifecycle,
                str(archive),
                str(install_root),
            ],
            check=True,
        )


if __name__ == "__main__":
    run()
