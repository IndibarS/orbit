"""Non-blocking mirror benchmark worker used by the GTK page."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from http.client import HTTPException
from threading import Event, Thread
from time import monotonic, perf_counter
from urllib.request import Request, urlopen

from orbit_gtk.backend.mirror_catalogues import fetch_catalogue, validate_release
from orbit_gtk.backend.mirrors import MirrorCandidate, SourceSettings, flag
from orbit_gtk.backend.models import MirrorInfo
from orbit_gtk.backend.network import read_response

MAX_WORKERS = 25
TIMEOUT_SECONDS = 5.0


def get_flag_for_mirror(url_or_domain: str) -> str:
    """Infer a flag from a country-code top-level domain without downloading data."""
    domain = url_or_domain.split("//")[-1].split("/")[0].lower()
    return flag(domain.rsplit(".", 1)[-1]) if "." in domain else "🌐"


class MirrorBenchmarkWorker:
    """Benchmark mirrors in a worker thread and dispatch UI callbacks safely."""

    def __init__(
        self,
        suite: str,
        *,
        on_masterlist: Callable[[int], object] | None = None,
        on_result: Callable[[MirrorInfo], object] | None = None,
        on_progress: Callable[[int, int, str], object] | None = None,
        on_done: Callable[[list[MirrorInfo], str | None], object] | None = None,
        dispatch: Callable[..., object] | None = None,
        fetcher: Callable[[], list[MirrorCandidate]] | None = None,
        settings: SourceSettings | None = None,
        https_only: bool = False,
        countries: tuple[str, ...] = (),
    ) -> None:
        self.https_only = https_only
        self.countries = {c.upper() for c in countries}
        self.suite = suite
        self._on_masterlist = on_masterlist
        self._on_result = on_result
        self._on_progress = on_progress
        self._on_done = on_done
        self._dispatch = dispatch or (lambda callback, *args: callback(*args))
        self.settings = settings or SourceSettings(suite, ("main",))
        self._fetcher = fetcher or (lambda: fetch_catalogue(self.settings))
        self._cancelled = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start one benchmark run."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="orbit-mirror-benchmark", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request cancellation; in-flight requests end at their short timeout."""
        self._cancelled.set()

    def _emit(self, callback: Callable[..., object] | None, *args: object) -> None:
        if callback and not self._cancelled.is_set():
            self._dispatch(callback, *args)

    def _run(self) -> None:
        try:
            mirrors = self._fetcher()
            if self.countries:
                mirrors = [m for m in mirrors if m.country_code.upper() in self.countries]
        except Exception as error:  # The page needs an actionable failure, not a traceback.
            self._done([], str(error))
            return

        if self._cancelled.is_set():
            return
        self._emit(self._on_masterlist, len(mirrors))
        results: list[MirrorInfo] = []
        total = len(mirrors)
        completed = 0
        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS, thread_name_prefix="orbit-mirror"
        ) as executor:
            futures: dict[Future[MirrorInfo], MirrorCandidate] = {
                executor.submit(self._benchmark_one, mirror): mirror for mirror in mirrors
            }
            for future in as_completed(futures):
                if self._cancelled.is_set():
                    for pending in futures:
                        pending.cancel()
                    return
                mirror = futures[future]
                completed += 1
                self._emit(self._on_progress, completed, total, mirror.url)
                try:
                    result = future.result()
                except Exception:
                    result = MirrorInfo(
                        domain=mirror.domain,
                        url=mirror.url,
                        country_code=mirror.country_code,
                        latency_ms=float("inf"),
                    )
                results.append(result)
                if result.reachable:
                    self._emit(self._on_result, result)

        reachable = sorted(
            (item for item in results if item.reachable), key=lambda item: item.latency_ms
        )
        unreachable = [item for item in results if not item.reachable]
        self._done(reachable + unreachable, None)

    def _done(self, results: list[MirrorInfo], error: str | None) -> None:
        if self._on_done and not self._cancelled.is_set():
            self._dispatch(self._on_done, results, error)

    def _benchmark_one(self, mirror: MirrorCandidate) -> MirrorInfo:
        candidates = (mirror.url.replace("http://", "https://", 1), mirror.url)
        for base_url in dict.fromkeys(candidates):
            if self.https_only and not base_url.startswith("https://"):
                continue
            if self._cancelled.is_set():
                break
            release_url = f"{base_url}/dists/{self.suite}/Release"
            request = Request(release_url, headers={"User-Agent": "Orbit-Package-Manager/1.0"})
            started = perf_counter()
            deadline = monotonic() + TIMEOUT_SECONDS
            try:
                with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    if response.status != 200:
                        continue
                    release = read_response(
                        response,
                        max_bytes=2_000_000,
                        deadline=deadline,
                        cancelled=self._cancelled.is_set,
                    )
                    validate_release(release, self.settings)
            except (OSError, HTTPException, ValueError):
                continue
            latency = (perf_counter() - started) * 1_000
            return MirrorInfo(
                domain=mirror.domain,
                url=base_url,
                country_code=mirror.country_code,
                latency_ms=round(latency, 1),
            )
        return MirrorInfo(
            domain=mirror.domain,
            url=mirror.url,
            country_code=mirror.country_code,
            latency_ms=float("inf"),
        )
