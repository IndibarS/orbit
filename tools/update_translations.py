"""Extract Orbit UI messages and compile contributed gettext catalogues."""

import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
locales = root / "orbit_gtk/locales"
locales.mkdir(exist_ok=True)
subprocess.run(
    [
        "xgettext",
        "--language=Python",
        "--from-code=UTF-8",
        "--keyword=tr",
        "--keyword=ntr:1,2",
        "--package-name=Orbit",
        "--output",
        str(locales / "orbit.pot"),
        *map(str, (root / "orbit_gtk/ui").rglob("*.py")),
    ],
    check=True,
)
for source in locales.glob("*/LC_MESSAGES/orbit.po"):
    subprocess.run(
        ["msgfmt", "--check", "-o", str(source.with_suffix(".mo")), str(source)], check=True
    )
