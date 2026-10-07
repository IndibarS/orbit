"""Official distro catalogues and bounded repository compatibility checks."""

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPException
from time import monotonic
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from orbit_gtk.backend.mirrors import (
    PROVIDERS,
    MirrorCandidate,
    MirrorDiscoveryError,
    SourceSettings,
    _field_map,
    fetch_masterlist,
    normalize_mirror_url,
)
from orbit_gtk.backend.network import read_response


class CatalogueParser(HTMLParser):
    """Read package links, excluding download images and navigation links."""

    def __init__(self, provider):
        super().__init__()
        self.provider = provider
        self.urls = []
        self.in_table = 0
        self.in_cell = False
        self.href = None
        self.mint_section = False
        self.mint_finished = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.in_table += 1
        elif tag == "td":
            self.in_cell = True
        elif tag == "a":
            self.href = attrs.get("href")

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = max(0, self.in_table - 1)
            if self.mint_section:
                self.mint_finished = True
        elif tag == "td":
            self.in_cell = False
        elif tag == "a":
            self.href = None

    def handle_data(self, data):
        text = data.strip()
        if text == "Repository mirrors":
            self.mint_section = True
        if not self.in_table or not self.in_cell:
            return
        if self.provider == "linuxmint":
            if (
                self.mint_section
                and not self.mint_finished
                and text.startswith(("http://", "https://"))
            ):
                self.urls.append(text)
        elif self.provider == "ubuntu":
            if self.href and text in {"http", "https"}:
                self.urls.append(self.href)
        elif self.provider == "kali":
            if self.href and text == self.href and text.startswith(("http://", "https://")):
                self.urls.append(self.href)


def parse_devuan_catalogue(raw, archive):
    """Parse Devuan's live tagfile; BaseURL is a root containing both archives."""
    if archive not in {"merged", "devuan"}:
        raise ValueError("Unrecognized Devuan archive layout.")
    candidates = {}
    # Some adjacent records have no blank separator; FQDN starts each record.
    for block in re.split(r"(?im)(?=^FQDN:)", raw):
        fields = _field_map(block)
        if fields.get("active", "").casefold() != "yes":
            continue
        protocols = {p.strip().upper() for p in fields.get("protocols", "").split("|")}
        scheme = "https" if "HTTPS" in protocols else "http" if "HTTP" in protocols else None
        base = fields.get("baseurl", "").strip().rstrip("/")
        if not scheme or not base or "://" in base:
            continue
        try:
            url = normalize_mirror_url(f"{scheme}://{base}/{archive}")
        except ValueError:
            continue
        country = fields.get("countrycode", "").split("|")[0].strip().upper()
        parsed = urlparse(url)
        key = (parsed.netloc, parsed.path)
        if key not in candidates or scheme == "https":
            candidates[key] = MirrorCandidate(parsed.netloc, url, country)
        if len(candidates) > 2000:
            raise MirrorDiscoveryError("Mirror catalogue exceeds the supported size.")
    return list(candidates.values())


def parse_catalogue(raw, provider, *, archive="merged"):
    if provider == "devuan":
        return parse_devuan_catalogue(raw, archive)
    if provider == "ubuntu" and "<rss" in raw:
        if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
            raise ValueError("Unsupported XML declarations")
        root = ElementTree.fromstring(raw)
        mirrors = {}
        for item in root.findall("./channel/item"):
            try:
                url = normalize_mirror_url(item.findtext("link") or "")
            except ValueError:
                continue
            country = next(
                (node.text or "" for node in item.iter() if node.tag.endswith("}countrycode")), ""
            )
            parsed = urlparse(url)
            key = (parsed.netloc, parsed.path)
            if key not in mirrors or parsed.scheme == "https":
                mirrors[key] = MirrorCandidate(parsed.netloc, url, country.upper())
            if len(mirrors) > 2000:
                raise MirrorDiscoveryError("Mirror catalogue exceeds the supported size.")
        return list(mirrors.values())
    parser = CatalogueParser(provider)
    parser.feed(raw)
    candidates = {}
    for value in parser.urls:
        try:
            url = normalize_mirror_url(value)
        except ValueError:
            continue
        parsed = urlparse(url)
        key = (parsed.netloc, parsed.path)
        if key not in candidates or parsed.scheme == "https":
            candidates[key] = MirrorCandidate(parsed.netloc, url, "")
    if len(candidates) > 2000:
        raise MirrorDiscoveryError("Mirror catalogue exceeds the supported size.")
    return list(candidates.values())


def fetch_catalogue(settings: SourceSettings, timeout=15.0):
    if settings.provider == "debian":
        return fetch_masterlist(timeout, architectures=settings.architectures or None)
    name, _, url = PROVIDERS[settings.provider]
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "Orbit-Package-Manager/1.0"}),
            timeout=min(timeout, 5),
        ) as response:
            raw = read_response(
                response, max_bytes=2_000_000, deadline=monotonic() + timeout
            ).decode("utf-8", errors="replace")
        candidates = parse_catalogue(raw, settings.provider, archive=settings.archive)
        if not candidates:
            raise ValueError("The catalogue contains no recognized package mirrors.")
        return candidates
    except (OSError, HTTPException, ValueError) as error:
        raise MirrorDiscoveryError(
            f"Unable to download {name}'s mirror catalogue: {error}"
        ) from error


def validate_release(release: bytes, settings: SourceSettings):
    """Check compatibility; APT still authenticates signatures on index refresh."""
    fields = _field_map(release.decode("utf-8", errors="replace"))
    origin = PROVIDERS[settings.provider][1]
    if fields.get("origin", "").casefold() != origin.casefold():
        raise ValueError("Mirror belongs to a different distribution.")
    if settings.provider == "devuan":
        label = {"merged": "Devuan", "devuan": "Master"}.get(settings.archive)
        if not label or fields.get("label") != label:
            raise ValueError("Mirror serves a different Devuan archive layout.")
    if settings.suite not in {fields.get("suite"), fields.get("codename")}:
        if not (
            settings.provider == "debian"
            and settings.suite == "sid"
            and fields.get("suite") == "unstable"
        ):
            raise ValueError("Mirror does not serve the selected suite.")
    if not fields.get("sha256"):
        raise ValueError("Mirror has no SHA256 repository index.")
    if not set(settings.components).issubset(fields.get("components", "").split()):
        raise ValueError("Mirror is missing configured archive components.")
    if not set(settings.architectures).issubset(fields.get("architectures", "").split()):
        raise ValueError("Mirror does not support the configured architectures.")
    if settings.source_packages:
        indexes = fields.get("sha256", "")
        if any(f"{component}/source/Sources" not in indexes for component in settings.components):
            raise ValueError("Mirror is missing configured source-package indexes.")
    if fields.get("valid-until"):
        try:
            expiry = parsedate_to_datetime(fields["valid-until"])
            if expiry.tzinfo is None or expiry < datetime.now(UTC):
                raise ValueError("Mirror repository metadata has expired.")
        except (TypeError, OverflowError) as error:
            raise ValueError("Mirror has an invalid metadata expiry.") from error


def validate_mirror(url, settings):
    """Recheck selections in the helper before changing its own source file."""
    url = normalize_mirror_url(url)
    request = Request(
        f"{url}/dists/{settings.suite}/Release", headers={"User-Agent": "Orbit-Package-Manager/1.0"}
    )
    deadline = monotonic() + 5
    with urlopen(request, timeout=5) as response:
        body = read_response(response, max_bytes=2_000_000, deadline=deadline)
    validate_release(body, settings)
