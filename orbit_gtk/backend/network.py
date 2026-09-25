"""Size-limited metadata reads that notice slow trickles and incomplete bodies."""

from collections.abc import Callable
from time import monotonic


def read_response(
    response, *, max_bytes: int, deadline: float, cancelled: Callable[[], bool] | None = None
) -> bytes:
    length = response.headers.get("Content-Length")
    expected = None
    if length is not None:
        try:
            expected = int(length)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid download length") from error
        if expected < 0:
            raise ValueError("Invalid download length")
        if expected > max_bytes:
            raise ValueError("Download is too large")
    data = bytearray()
    # HTTPResponse.read(n) can wait for n bytes while a server trickles data.
    # read1 returns after one buffered/socket read so we can check the deadline.
    read = response.read1
    while True:
        if cancelled and cancelled():
            raise InterruptedError("Download cancelled")
        if monotonic() >= deadline:
            raise TimeoutError("Download timed out")
        chunk = read(min(65536, max_bytes + 1 - len(data)))
        if monotonic() >= deadline:
            raise TimeoutError("Download timed out")
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise ValueError("Download is too large")
    if expected is not None and len(data) != expected:
        raise ConnectionError("Download was interrupted before completion")
    return bytes(data)
