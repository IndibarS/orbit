"""Bounded downloads of screenshot URLs supplied by AppStream."""

from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from orbit_gtk.backend.network import read_response

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def valid_image_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return bool(
            parts.scheme == "https"
            and parts.hostname
            and not parts.username
            and not parts.password
            and not any(c.isspace() for c in url)
        )
    except ValueError:
        return False


class _ImageRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_image_url(newurl):
            raise ValueError("Unsupported screenshot redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_screenshot(url: str) -> bytes:
    if not valid_image_url(url):
        raise ValueError("Unsupported screenshot URL")
    request = Request(url, headers={"User-Agent": "Orbit-GTK/0.2", "Accept": "image/*"})
    deadline = monotonic() + 20
    with build_opener(_ImageRedirect()).open(request, timeout=8) as response:
        return read_response(response, max_bytes=MAX_IMAGE_BYTES, deadline=deadline)
