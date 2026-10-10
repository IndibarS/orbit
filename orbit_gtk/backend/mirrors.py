"""Distribution mirror discovery and source-file helpers.

The selection flow follows the useful parts of Nala's legacy ``fetch``
implementation: discover distro archives, preserve their source settings, and
measure compatible Release files without refreshing package indexes.
This module intentionally contains no GTK imports so that it is usable by the
privileged helper and straightforward to test.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
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
    """Raised when a distribution mirror catalogue cannot be obtained."""


@dataclass(frozen=True, slots=True)
class MirrorCandidate:
    """A package archive advertised by a distribution catalogue."""

    domain: str
    url: str
    country_code: str


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """The parts of an archive source stanza that Orbit must preserve."""

    suite: str
    components: tuple[str, ...]
    signed_by: str | None = None
    provider: str = "debian"
    architectures: tuple[str, ...] = ()
    uris: tuple[str, ...] = field(default=(), compare=False)
    archive: str = "merged"
    source_packages: bool = False

    @property
    def repository(self):
        suffix = f":{self.archive}" if self.provider == "devuan" else ""
        return f"{self.provider}:{self.suite}{suffix}"

    @property
    def label(self):
        suffix = f" · {self.archive}" if self.provider == "devuan" else ""
        return f"{PROVIDERS[self.provider][0]} · {self.suite}{suffix}"


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


class UnsupportedMirrorDistribution(ValueError):
    """No enabled archive can be matched to a supported mirror provider."""


PROVIDERS = {
    "devuan": ("Devuan", "Devuan", "https://pkgmaster.devuan.org/mirror_list.txt"),
    "debian": ("Debian", "Debian", "https://mirror-master.debian.org/status/Mirrors.masterlist"),
    "ubuntu": ("Ubuntu", "Ubuntu", "https://launchpad.net/ubuntu/+archivemirrors-rss"),
    "linuxmint": ("Linux Mint", "linuxmint", "https://www.linuxmint.com/mirrors.php"),
    "kali": ("Kali", "Kali", "https://http.kali.org/README?mirrorlist"),
}
KEY_PREFIXES = {
    "devuan": "devuan-archive-",
    "debian": "debian-archive-",
    "ubuntu": "ubuntu-archive-",
    "linuxmint": "linuxmint-",
    "kali": "kali-archive-",
}
OFFICIAL_HOSTS = {
    "devuan": {"deb.devuan.org", "pkgmaster.devuan.org"},
    "debian": {"deb.debian.org", "ftp.debian.org"},
    "ubuntu": {"archive.ubuntu.com", "ports.ubuntu.com"},
    "linuxmint": {"packages.linuxmint.com", "fastly.linuxmint.io"},
    "kali": {"http.kali.org", "kali.download", "archive.kali.org"},
}


def source_records(paths=None):
    """Read enabled binary stanzas from both APT source formats."""
    explicit_paths = paths is not None
    if paths is None:
        directory = Path("/etc/apt/sources.list.d")
        paths = [
            Path("/etc/apt/sources.list"),
            *sorted(directory.glob("*.list")),
            *sorted(directory.glob("*.sources")),
        ]
    for path in paths:
        if path == ORBIT_SOURCES_PATH and not explicit_paths:
            continue
        if path.suffix == ".sources":
            yield from (f for f in iter_deb822_sources(path) if "deb" in f.get("types", "").split())
            continue
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            match = re.match(r"^\s*deb\s+(?:\[([^]]*)\]\s+)?(\S+)\s+(\S+)\s+([^#]+)", line)
            if not match:
                continue
            options = dict(item.split("=", 1) for item in (match[1] or "").split() if "=" in item)
            fields = {"uris": match[2], "suites": match[3], "components": match[4].strip()}
            if "signed-by" in options:
                fields["signed-by"] = options["signed-by"]
            if "arch" in options:
                fields["architectures"] = options["arch"].replace(",", " ")
            # Do not propagate trust bypasses or reinterpret architecture modifiers.
            if any(k in options for k in ("trusted", "allow-insecure", "arch+", "arch-")):
                continue
            yield fields


def source_profiles(identity=None, paths=None, lists_dir=Path("/var/lib/apt/lists")):
    """Discover real distro repositories offline, never infer suites from ID_LIKE."""
    import apt_pkg

    if identity is None:
        try:
            identity = platform.freedesktop_os_release()
        except OSError:
            identity = {}
    distro = identity.get("ID")
    preferred = {
        "devuan": ("devuan",),
        "debian": ("debian",),
        "ubuntu": ("ubuntu",),
        "kali": ("kali",),
        "linuxmint": ("linuxmint", "ubuntu", "debian"),
    }.get(distro, ())
    # OS identity orders the UI only. Each enabled archive supplies its own identity.
    supported = tuple(dict.fromkeys((*preferred, *PROVIDERS)))
    profiles = {}
    enabled_arches = get_architectures()
    for fields in source_records(paths):
        if any(
            k in fields
            for k in ("trusted", "allow-insecure", "architectures-add", "architectures-remove")
        ):
            continue
        key = fields.get("signed-by") or None
        for uri in fields.get("uris", "").split():
            try:
                uri = normalize_mirror_url(uri)
            except ValueError:
                continue
            host = urlparse(uri).hostname
            for suite in fields.get("suites", "").split():
                if not _SUITE_RE.fullmatch(suite) or suite.endswith(("-security", "/updates")):
                    continue  # Dedicated security sources are never mirrored by Orbit.
                origin = None
                for release in ("InRelease", "Release"):
                    file = lists_dir / apt_pkg.uri_to_filename(f"{uri}/dists/{suite}/{release}")
                    try:
                        metadata = file.read_text()
                    except FileNotFoundError:
                        continue
                    except (OSError, UnicodeError):
                        origin = ""
                        break
                    origins = re.findall(
                        r"^Origin:[ \t]*([^\r\n]*)", metadata, re.MULTILINE | re.IGNORECASE
                    )
                    # Existing but ambiguous metadata must not fall back to host/key guesses.
                    origin = origins[0].strip() if len(origins) == 1 else ""
                    break
                if origin == "":
                    continue
                for provider in supported:
                    key_matches = bool(key) and all(
                        Path(k).name.startswith(KEY_PREFIXES[provider])
                        and k.endswith((".gpg", ".pgp", ".asc"))
                        and k.startswith("/")
                        for k in key.split()
                    )
                    if key and not key_matches:
                        continue
                    known_host = host in OFFICIAL_HOSTS[provider] or (
                        provider == "ubuntu" and host and host.endswith(".archive.ubuntu.com")
                    )
                    if origin:
                        matches = origin.casefold() == PROVIDERS[provider][1].casefold()
                    else:
                        matches = known_host or key_matches
                    if not matches:
                        continue
                    archive = "merged"
                    if provider == "devuan":
                        archive = urlparse(uri).path.rstrip("/").rsplit("/", 1)[-1]
                        if archive not in {"merged", "devuan"}:
                            continue  # Do not replace a custom archive layout by guessing.
                    components = tuple(fields.get("components", "main").split())
                    restrictions = fields.get("architectures", "").split()
                    arches = tuple(
                        a for a in enabled_arches if not restrictions or a in restrictions
                    )
                    if not arches:
                        continue
                    settings = SourceSettings(
                        suite,
                        components,
                        key,
                        provider,
                        arches,
                        (uri,),
                        archive,
                        "deb-src" in fields.get("types", "").split(),
                    )
                    old = profiles.get(settings.repository)
                    if old and (
                        old.signed_by != settings.signed_by
                        or set(old.architectures) != set(settings.architectures)
                    ):
                        raise ValueError(
                            f"Conflicting signing keys or architecture restrictions for {settings.label}; "
                            "align those settings in APT sources before selecting mirrors."
                        )
                    if old:
                        # Multiple stanzas may enable different components or deb-src.
                        # They describe the same archive, not conflicting trust settings.
                        settings = replace(
                            settings,
                            components=tuple(dict.fromkeys((*old.components, *settings.components))),
                            source_packages=old.source_packages or settings.source_packages,
                            uris=tuple(dict.fromkeys((*old.uris, uri))),
                        )
                    profiles[settings.repository] = settings
                    break
    if paths is None and ORBIT_SOURCES_PATH.exists():
        try:
            owned = source_profiles(identity, [ORBIT_SOURCES_PATH], lists_dir)
        except UnsupportedMirrorDistribution:
            owned = []
        for settings in owned:
            previous = profiles.get(settings.repository)
            if previous:
                settings = replace(
                    settings, uris=tuple(dict.fromkeys((*previous.uris, *settings.uris)))
                )
            profiles[settings.repository] = settings
    if not profiles:
        raise UnsupportedMirrorDistribution(
            "No recognized Debian, Ubuntu, Devuan, Linux Mint or Kali archives found. "
            "Refresh package lists if archive metadata is missing, then try again. "
            "Unrecognized repositories remain unchanged."
        )
    return sorted(profiles.values(), key=lambda p: (supported.index(p.provider), p.suite))


def source_settings(repository=None) -> SourceSettings:
    if repository and repository.startswith("manual:"):
        parts = repository.split(":")
        if len(parts) != 5:
            raise ValueError("Invalid manual archive selection")
        _, provider, suite, archive, _marker = parts
        if (
            provider not in PROVIDERS
            or archive not in {"merged", "devuan"}
            or _marker != "explicit"
        ):
            raise ValueError("Unsupported manual archive selection")
        suite = validate_suite(suite)
        try:
            profiles = source_profiles()
        except UnsupportedMirrorDistribution:
            profiles = []
        template = next((p for p in profiles if p.provider == provider), None)
        if template:
            return replace(template, suite=suite, archive=archive, uris=())
        keys = sorted(Path("/usr/share/keyrings").glob(KEY_PREFIXES[provider] + "keyring.*"))
        key = next((k for k in keys if k.is_file() and k.suffix in {".gpg", ".pgp", ".asc"}), None)
        if key is None:
            raise ValueError(
                f"Install the distribution's official {PROVIDERS[provider][0]} archive keyring before adding this repository."
            )
        return SourceSettings(
            suite, ("main",), str(key), provider, get_architectures(), archive=archive
        )
    profiles = source_profiles()
    if repository is None:
        return profiles[0]
    for settings in profiles:
        if settings.repository == repository:
            return settings
    raise ValueError("The selected repository is no longer configured. Reload the Mirrors page.")


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


def fetch_masterlist(timeout: float = 15.0, architectures=None) -> list[MirrorCandidate]:
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
    return parse_masterlist(raw, architectures or get_architectures())


def read_orbit_mirrors(
    path: Path = ORBIT_SOURCES_PATH, settings: SourceSettings | None = None
) -> list[str]:
    """Return only the mirrors configured by Orbit, never unrelated user sources."""
    urls: list[str] = []
    for fields in iter_deb822_sources(path):
        if settings and not matches_profile(fields, settings):
            continue
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
    if settings.provider not in PROVIDERS:
        raise ValueError("Unknown mirror provider.")
    if any(not _SUITE_RE.fullmatch(component) for component in settings.components):
        raise ValueError("Invalid archive component.")
    if settings.signed_by and ("\n" in settings.signed_by or "\r" in settings.signed_by):
        raise ValueError("Invalid archive signing key.")
    components = " ".join(settings.components or ("main",))
    lines = [
        "# Managed by Orbit Package Manager. Other APT source files are untouched.",
        f"X-Orbit-Repository: {settings.repository}",
        "Types: deb deb-src" if settings.source_packages else "Types: deb",
        f"URIs: {' '.join(normalized)}",
        f"Suites: {suite}",
        f"Components: {components}",
    ]
    if settings.architectures:
        if any(not _SUITE_RE.fullmatch(arch) for arch in settings.architectures):
            raise ValueError("Invalid architecture.")
        lines.append(f"Architectures: {' '.join(settings.architectures)}")
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


def matches_profile(fields, settings):
    marker = fields.get("x-orbit-repository")
    if marker:
        return marker == settings.repository
    # Migrate the original Debian-only Orbit stanza without touching other suites.
    return settings.provider == "debian" and fields.get("suites") == settings.suite


def replace_profile(content, urls, settings):
    """Replace only one repository's owned stanza, preserving other selections."""
    paragraphs = [
        part
        for part in re.split(r"\n\s*\n", content.strip())
        if part.strip() and not matches_profile(_field_map(part), settings)
    ]
    rendered = render_orbit_source(urls, settings)
    if rendered:
        paragraphs.append(rendered.strip())
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")
