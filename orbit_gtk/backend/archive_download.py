"""Bounded user-side archive download; downloaded files still require local-install review."""

import hashlib
from time import monotonic
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def download_archive(
    url, destination, *, cancelled=lambda: False, progress=lambda *_: None, sha256=None
):
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Enter an HTTP(S) package URL without credentials.")
    maximum = 2 * 1024 * 1024 * 1024
    deadline = monotonic() + 600
    digest = hashlib.sha256()
    try:
        with (
            urlopen(
                Request(url, headers={"User-Agent": "Orbit-Package-Manager/1.0"}), timeout=15
            ) as response,
            destination.open("wb") as output,
        ):
            if urlparse(response.geturl()).scheme not in {"http", "https"}:
                raise ValueError("Unsupported redirect")
            expected = int(response.headers.get("Content-Length", "0"))
            if expected < 0 or expected > maximum:
                raise ValueError("Archive exceeds the 2 GiB download limit")
            count = 0
            while True:
                if cancelled():
                    raise InterruptedError("Download cancelled")
                if monotonic() > deadline:
                    raise TimeoutError("Archive download timed out")
                chunk = response.read1(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum:
                    raise ValueError("Archive exceeds the 2 GiB download limit")
                output.write(chunk)
                digest.update(chunk)
                progress(count, expected)
            if expected and count != expected:
                raise ConnectionError("Incomplete archive download")
            if sha256 and digest.hexdigest().lower() != sha256.lower():
                raise ValueError("Archive SHA256 does not match")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
