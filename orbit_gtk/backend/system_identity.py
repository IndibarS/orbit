"""Display OS identity separately from enabled Debian repository suites."""

import platform
import re
from pathlib import Path
from urllib.parse import urlparse

import apt_pkg

from orbit_gtk.backend.mirrors import iter_deb822_sources


def debian_suites(
    source_list=Path("/etc/apt/sources.list"),
    source_dir=Path("/etc/apt/sources.list.d"),
    lists_dir=Path("/var/lib/apt/lists"),
):
    suites = set()

    def add(uris, candidates, signed_by=""):
        # A vendor may also serve /debian. Its explicitly separate signing key
        # must not turn e.g. a Sid system into a mixed Sid/stable system.
        if signed_by and not any(
            Path(key).name.startswith("debian-archive-") and key.endswith((".gpg", ".asc"))
            for key in signed_by.replace(",", " ").split()
        ):
            return
        # Exclude vendor feeds, Debian security and source-only entries. This
        # recognizes the standard Debian archive layout, including its mirrors.
        if not any(urlparse(uri).path.rstrip("/") == "/debian" for uri in uris):
            return
        for suite in candidates:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", suite):
                continue
            if suite.endswith(("-security", "-updates", "-backports", "-backports-sloppy")):
                continue
            recognized = False
            for uri in uris:
                origin = None
                for release in ("InRelease", "Release"):
                    path = lists_dir / apt_pkg.uri_to_filename(
                        f"{uri.rstrip('/')}/dists/{suite}/{release}"
                    )
                    try:
                        metadata = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    field = re.search(r"^Origin:\s*(.+)$", metadata, re.MULTILINE)
                    if field:
                        origin = field[1].strip()
                        break
                host = urlparse(uri).hostname or ""
                recognized |= (
                    origin == "Debian"
                    if origin is not None
                    else bool(signed_by or host == "deb.debian.org" or host.endswith(".debian.org"))
                )
            if recognized:
                suites.add("sid" if suite == "unstable" else suite)

    for path in sorted(source_dir.glob("*.sources")):
        for fields in iter_deb822_sources(path):
            if "deb" in fields.get("types", "").split():
                add(
                    fields.get("uris", "").split(),
                    fields.get("suites", "").split(),
                    fields.get("signed-by", ""),
                )
    for path in [source_list, *sorted(source_dir.glob("*.list"))]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = re.match(r"^\s*deb\s+(?:\[[^]]*\]\s*)?(\S+)\s+(\S+)\s+[^#]+", line)
            if match:
                signing = re.search(r"\bsigned-by=([^\s\]]+)", line)
                add([match[1]], [match[2]], signing[1] if signing else "")
    return sorted(suites)


def system_identity(identity=None, **source_paths):
    if identity is None:
        try:
            identity = platform.freedesktop_os_release()
        except (OSError, ValueError):
            identity = {}
    name = identity.get("NAME") or identity.get("ID") or "Linux"
    pretty = identity.get("PRETTY_NAME") or name
    codename = identity.get("VERSION_CODENAME") or identity.get("DEBIAN_CODENAME")
    label = f"OS codename: {codename}" if codename else "Release unknown"
    distro = pretty
    if identity.get("ID") == "debian":
        suites = debian_suites(**source_paths)
        if len(suites) == 1:
            label = "Sid (unstable)" if suites[0] == "sid" else suites[0]
            distro = f"{name} · {label}"
        elif suites:
            label = "Mixed Debian suites: " + ", ".join(suites)
            distro = f"{name} · {label}"
    return {"distro": distro, "distro_name": name, "suite": label}
