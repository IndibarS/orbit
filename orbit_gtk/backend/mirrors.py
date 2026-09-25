"""Debian mirror discovery and source-file helpers.

The selection flow follows the useful parts of Nala's legacy ``fetch``
implementation: use Debian's master list, keep only mirrors which advertise
every enabled architecture, and measure the Release file for the active suite.
This module intentionally contains no GTK imports so that it is usable by the
privileged helper and straightforward to test.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Iterable
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from time import monotonic
from typing import Final
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from orbit_gtk.backend.network import read_response

MASTERLIST_URL: Final = "https://mirror-master.debian.org/status/Mirrors.masterlist"
ORBIT_SOURCES_PATH: Final = Path("/etc/apt/sources.list.d/orbit-mirrors.sources")
_SUITE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")

COUNTRY_FLAGS: Final[dict[str, str]] = {
    "AD": "🇦🇩",
    "AE": "🇦🇪",
    "AR": "🇦🇷",
    "AT": "🇦🇹",
    "AU": "🇦🇺",
    "AZ": "🇦🇿",
    "BA": "🇧🇦",
    "BD": "🇧🇩",
    "BE": "🇧🇪",
    "BG": "🇧🇬",
    "BR": "🇧🇷",
    "BY": "🇧🇾",
    "CA": "🇨🇦",
    "CH": "🇨🇭",
    "CL": "🇨🇱",
    "CN": "🇨🇳",
    "CO": "🇨🇴",
    "CZ": "🇨🇿",
    "DE": "🇩🇪",
    "DK": "🇩🇰",
    "DZ": "🇩🇿",
    "EC": "🇪🇨",
    "EE": "🇪🇪",
    "EG": "🇪🇬",
    "ES": "🇪🇸",
    "FI": "🇫🇮",
    "FR": "🇫🇷",
    "GB": "🇬🇧",
    "GR": "🇬🇷",
    "HK": "🇭🇰",
    "HR": "🇭🇷",
    "HU": "🇭🇺",
    "ID": "🇮🇩",
    "IE": "🇮🇪",
    "IL": "🇮🇱",
    "IN": "🇮🇳",
    "IR": "🇮🇷",
    "IS": "🇮🇸",
    "IT": "🇮🇹",
    "JP": "🇯🇵",
    "KE": "🇰🇪",
    "KR": "🇰🇷",
    "KZ": "🇰🇿",
    "LT": "🇱🇹",
    "LU": "🇱🇺",
    "LV": "🇱🇻",
    "MA": "🇲🇦",
    "MD": "🇲🇩",
    "MK": "🇲🇰",
    "MT": "🇲🇹",
    "MX": "🇲🇽",
    "MY": "🇲🇾",
    "NG": "🇳🇬",
    "NL": "🇳🇱",
    "NO": "🇳🇴",
    "NP": "🇳🇵",
    "NZ": "🇳🇿",
    "PH": "🇵🇭",
    "PK": "🇵🇰",
    "PL": "🇵🇱",
    "PT": "🇵🇹",
    "RO": "🇷🇴",
    "RS": "🇷🇸",
    "RU": "🇷🇺",
    "SA": "🇸🇦",
    "SE": "🇸🇪",
    "SG": "🇸🇬",
    "SI": "🇸🇮",
    "SK": "🇸🇰",
    "TH": "🇹🇭",
    "TR": "🇹🇷",
    "TW": "🇹🇼",
    "UA": "🇺🇦",
    "US": "🇺🇸",
    "UY": "🇺🇾",
    "UZ": "🇺🇿",
    "VN": "🇻🇳",
    "ZA": "🇿🇦",
    "ZM": "🇿🇲",
    "ZW": "🇿🇼",
}


class MirrorDiscoveryError(RuntimeError):
    """Raised when Debian's master mirror list cannot be obtained."""


@dataclass(frozen=True, slots=True)
class MirrorCandidate:
    """A mirror advertised by Debian's master list."""

    domain: str
    url: str
    country_code: str


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """The parts of a Debian source stanza that Orbit must preserve."""

    suite: str
    components: tuple[str, ...]
    signed_by: str | None = None


def flag(country_code: str) -> str:
    """Return a country flag without doing network I/O."""
    return COUNTRY_FLAGS.get(country_code.upper(), "🌐")


def get_architectures() -> tuple[str, ...]:
    """Return every architecture enabled in APT, with a portable fallback."""
    try:
        import apt_pkg

        apt_pkg.init()
        architectures = tuple(apt_pkg.get_architectures())
        if architectures:
            return architectures
    except (ImportError, SystemError):
        pass

    machine = platform.machine().lower()
    return (
        {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armhf", "i686": "i386"}.get(
            machine, machine
        ),
    )


def normalize_mirror_url(url: str) -> str:
    """Validate and normalize a mirror URL accepted in an APT source file."""
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url
    ):
        raise ValueError("Mirror URLs cannot contain whitespace or control characters.")
    parsed = urlparse(url)
    try:
        _ = parsed.port  # urllib validates the port lazily.
    except ValueError as error:
        raise ValueError("Invalid mirror port.") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.params
    ):
        raise ValueError(f"Invalid mirror URL: {url!r}")
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def validate_suite(suite: str) -> str:
    """Validate an APT suite before it is written into a Deb822 source file."""
    suite = suite.strip()
    if not _SUITE_RE.fullmatch(suite):
        raise ValueError(f"Invalid APT suite: {suite!r}")
    return suite


def _field_map(paragraph: str) -> dict[str, str]:
    """Parse the small Deb822 subset needed by Orbit, including continuations."""
    fields: dict[str, str] = {}
    current: str | None = None
    for line in paragraph.splitlines():
        if line.startswith((" ", "\t")) and current:
            fields[current] = f"{fields[current]} {line.strip()}".strip()
            continue
        if ":" not in line:
            current = None
            continue
        key, value = line.split(":", 1)
        current = key.strip().lower()
        fields[current] = value.strip()
    return fields


def iter_deb822_sources(path: Path) -> Iterable[dict[str, str]]:
    """Yield enabled Deb822 source paragraphs from *path* when it is readable."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    for paragraph in re.split(r"\n\s*\n", content):
        fields = _field_map(paragraph)
        enabled = fields.get("enabled", "yes").lower()
        if fields and enabled not in {"no", "false", "0"}:
            yield fields


def _source_paths() -> Iterable[Path]:
    preferred = Path("/etc/apt/sources.list.d/debian.sources")
    if preferred.is_file():
        yield preferred
    source_dir = preferred.parent
    try:
        for path in sorted(source_dir.glob("*.sources")):
            if path != preferred and path != ORBIT_SOURCES_PATH:
                yield path
    except OSError:
        return


def source_settings() -> SourceSettings:
    """Infer the active Debian suite/components without mutating user sources."""
    identity = platform.freedesktop_os_release()
    if identity.get("ID") != "debian":
        raise ValueError("Mirror selection currently supports Debian only.")
    for path in _source_paths():
        for fields in iter_deb822_sources(path):
            if "deb" not in fields.get("types", "deb").split():
                continue
            if not any(
                urlparse(uri).path.rstrip("/") == "/debian"
                for uri in fields.get("uris", "").split()
            ):
                continue
            suites = fields.get("suites", "").split()
            suites = [
                suite
                for suite in suites
                if not suite.endswith(("-security", "-updates", "-backports"))
            ]
            if not suites:
                continue
            components = tuple(fields.get("components", "main").split()) or ("main",)
            return SourceSettings(
                suite=validate_suite(suites[0]),
                components=components,
                signed_by="/usr/share/keyrings/debian-archive-keyring.gpg",
            )

    # Traditional one-line Debian sources remain common on older installations.
    paths = [Path("/etc/apt/sources.list"), *Path("/etc/apt/sources.list.d").glob("*.list")]
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            match = re.match(r"^\s*deb\s+(?:\[[^]]*\]\s+)?(\S+)\s+(\S+)\s+([^#]+)", line)
            if match and urlparse(match[1]).path.rstrip("/") == "/debian":
                suite = validate_suite(match[2])
                if suite.endswith(("-security", "-updates", "-backports")):
                    continue
                return SourceSettings(
                    suite, tuple(match[3].split()), "/usr/share/keyrings/debian-archive-keyring.gpg"
                )
    raise ValueError("No main Debian archive source found. Configure Debian sources first.")


def detect_system_suite() -> str:
    """Determine Debian's suite from os-release, falling back safely to stable."""
    os_release = Path("/etc/os-release")
    try:
        values = dict(
            line.split("=", 1)
            for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines()
            if "=" in line
        )
    except OSError:
        return "stable"

    codename = values.get("VERSION_CODENAME") or values.get("DEBIAN_CODENAME")
    if codename:
        return codename.strip().strip('"')
    pretty_name = values.get("PRETTY_NAME", "").lower()
    return "sid" if "sid" in pretty_name or "unstable" in pretty_name else "stable"


def parse_masterlist(raw: str, architectures: Iterable[str]) -> list[MirrorCandidate]:
    """Parse Debian's master list using Nala's all-enabled-architectures rule."""
    required_arches = tuple(architectures)
    candidates: list[MirrorCandidate] = []
    seen: set[str] = set()
    for block in raw.split("\n\n"):
        fields = _field_map(block)
        if not set(required_arches).issubset(fields.get("archive-architecture", "").split()):
            continue
        site = fields.get("site", "")
        archive_path = fields.get("archive-http", "")
        country = (fields.get("country", "").split(maxsplit=1) or [""])[0].upper()
        if not site or not archive_path:
            continue
        try:
            url = normalize_mirror_url(f"http://{site}{archive_path}")
        except ValueError:
            continue
        if url not in seen:
            seen.add(url)
            candidates.append(MirrorCandidate(site, url, country))
    return candidates


def fetch_masterlist(timeout: float = 15.0) -> list[MirrorCandidate]:
    """Fetch and parse Debian's authoritative mirror catalogue."""
    request = Request(MASTERLIST_URL, headers={"User-Agent": "Orbit-Package-Manager/1.0"})
    deadline = monotonic() + timeout
    try:
        with urlopen(request, timeout=min(timeout, 5)) as response:
            raw = read_response(response, max_bytes=2_000_000, deadline=deadline).decode(
                "utf-8", errors="replace"
            )
    except (OSError, HTTPException, ValueError) as error:
        raise MirrorDiscoveryError(f"Unable to download Debian's mirror list: {error}") from error
    return parse_masterlist(raw, get_architectures())


def read_orbit_mirrors(path: Path = ORBIT_SOURCES_PATH) -> list[str]:
    """Return only the mirrors configured by Orbit, never unrelated user sources."""
    urls: list[str] = []
    for fields in iter_deb822_sources(path):
        for url in fields.get("uris", "").split():
            try:
                normalized = normalize_mirror_url(url)
            except ValueError:
                continue
            if normalized not in urls:
                urls.append(normalized)
    return urls


def render_orbit_source(urls: Iterable[str], settings: SourceSettings) -> str:
    """Return a valid Deb822 source file for the managed Orbit mirror list."""
    normalized = list(dict.fromkeys(normalize_mirror_url(url) for url in urls))
    if not normalized:
        return ""
    suite = validate_suite(settings.suite)
    if any(not _SUITE_RE.fullmatch(component) for component in settings.components):
        raise ValueError("Invalid archive component.")
    if settings.signed_by and ("\n" in settings.signed_by or "\r" in settings.signed_by):
        raise ValueError("Invalid archive signing key.")
    components = " ".join(settings.components or ("main",))
    lines = [
        "# Managed by Orbit Package Manager. Other APT source files are untouched.",
        "Types: deb",
        f"URIs: {' '.join(normalized)}",
        f"Suites: {suite}",
        f"Components: {components}",
    ]
    if settings.signed_by:
        lines.append(f"Signed-By: {settings.signed_by}")
    return "\n".join(lines) + "\n"


def existing_mirror_urls(suite: str, paths=None) -> set[str]:
    """Read enabled binary sources, excluding Orbit's file, as Nala fetch does."""
    if paths is None:
        directory = Path("/etc/apt/sources.list.d")
        paths = [
            Path("/etc/apt/sources.list"),
            *directory.glob("*.list"),
            *directory.glob("*.sources"),
        ]
    found = set()
    for path in paths:
        if path == ORBIT_SOURCES_PATH:
            continue
        if path.suffix == ".sources":
            records = [
                (fields.get("uris", "").split(), fields.get("suites", "").split())
                for fields in iter_deb822_sources(path)
                if "deb" in fields.get("types", "").split()
            ]
        else:
            try:
                lines = path.read_text().splitlines()
            except FileNotFoundError:
                continue
            records = []
            for line in lines:
                match = re.match(r"^\s*deb\s+(?:\[[^]]*\]\s+)?(\S+)\s+(\S+)", line)
                if match:
                    records.append(([match[1]], [match[2]]))
        for urls, suites in records:
            if suite not in suites:
                continue
            for url in urls:
                try:
                    found.add(normalize_mirror_url(url))
                except ValueError:
                    continue
    return found
