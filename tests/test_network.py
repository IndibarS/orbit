"""Weak/offline network regressions; real HTTP tests use loopback only."""

import io
import os
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

import apt

from orbit_gtk.backend.mirror_benchmark import MirrorBenchmarkWorker
from orbit_gtk.backend.mirrors import MirrorCandidate, MirrorDiscoveryError, fetch_masterlist
from orbit_gtk.backend.network import read_response
from orbit_gtk.backend.transactions import configure_network, update


def response(body, headers=None):
    return SimpleNamespace(headers=headers or {}, read1=io.BytesIO(body).read1)


class NetworkTests(unittest.TestCase):
    def test_metadata_rejects_truncated_oversized_and_invalid_lengths(self):
        for header, error in (
            ("10", ConnectionError),
            ("100", ValueError),
            ("-1", ValueError),
            ("invalid", ValueError),
        ):
            with self.subTest(header=header), self.assertRaises(error):
                read_response(
                    response(b"abc", {"Content-Length": header}),
                    max_bytes=20,
                    deadline=time.monotonic() + 1,
                )
        with self.assertRaises(ValueError):
            read_response(response(b"012345"), max_bytes=5, deadline=time.monotonic() + 1)
        self.assertEqual(
            read_response(
                response(b"ok", {"Content-Length": "2"}), max_bytes=2, deadline=time.monotonic() + 1
            ),
            b"ok",
        )

    def test_metadata_stops_slow_trickle_and_cancellation(self):
        with patch("orbit_gtk.backend.network.monotonic", side_effect=[0, 0.4, 0.5, 1.1]):
            with self.assertRaises(TimeoutError):
                read_response(
                    SimpleNamespace(headers={}, read1=lambda _: b"x"), max_bytes=20, deadline=1
                )
        with self.assertRaises(InterruptedError):
            read_response(
                response(b"data"),
                max_bytes=10,
                deadline=time.monotonic() + 1,
                cancelled=lambda: True,
            )

    def test_mirror_dns_failure_and_cancellation_do_not_escape_worker(self):
        mirror = MirrorCandidate("example.invalid", "https://example.invalid/debian", "ZZ")
        completed = []
        worker = MirrorBenchmarkWorker(
            "sid",
            fetcher=lambda: [mirror],
            on_done=lambda results, error: completed.append((results, error)),
        )
        with patch(
            "orbit_gtk.backend.mirror_benchmark.urlopen",
            side_effect=URLError(socket.gaierror("DNS unavailable")),
        ):
            worker._run()
        self.assertEqual(len(completed), 1)
        self.assertFalse(completed[0][0][0].reachable)
        worker.stop()
        with patch("orbit_gtk.backend.mirror_benchmark.urlopen") as opener:
            self.assertFalse(worker._benchmark_one(mirror).reachable)
            opener.assert_not_called()
        with patch("orbit_gtk.backend.mirrors.urlopen", side_effect=URLError("Offline")):
            with self.assertRaisesRegex(MirrorDiscoveryError, "Unable to download"):
                fetch_masterlist()

    def test_apt_defaults_preserve_existing_network_configuration(self):
        values = {"Acquire::http::Timeout": "40", "Acquire::Retries": "0"}
        config = SimpleNamespace(
            exists=lambda key: key in values, set=lambda key, value: values.__setitem__(key, value)
        )
        with patch("orbit_gtk.backend.transactions.apt_pkg.config", config):
            configure_network()
        self.assertEqual(values["Acquire::http::Timeout"], "40")
        self.assertEqual(values["Acquire::Retries"], "0")
        self.assertEqual(values["Acquire::https::Timeout"], "20")

    def test_failed_refresh_is_actionable_and_does_not_claim_success(self):
        with (
            patch("orbit_gtk.backend.transactions.apt.Cache") as cache,
            patch("orbit_gtk.backend.transactions.apt_pkg.config"),
        ):
            cache.return_value.__enter__.return_value.update.side_effect = (
                apt.cache.FetchFailedException("Connection timed out")
            )
            events = []
            with self.assertRaisesRegex(RuntimeError, "cached package data"):
                update(lambda *args, **kwargs: events.append((args, kwargs)))
            self.assertFalse(any(args and args[0] == "complete" for args, _ in events))

    @unittest.skipUnless(
        os.environ.get("ORBIT_NETWORK_TESTS") == "1",
        "Enable loopback HTTP fault tests with ORBIT_NETWORK_TESTS=1",
    )
    def test_real_http_stalls_trickles_truncates_and_recovers(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                if self.path == "/cut":
                    self.send_header("Content-Length", "10")
                self.end_headers()
                try:
                    if self.path == "/stall":
                        time.sleep(0.3)
                    elif self.path == "/trickle":
                        for _ in range(30):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            time.sleep(0.03)
                    elif self.path == "/cut":
                        self.wfile.write(b"abc")
                    else:
                        self.wfile.write(b"ok")
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            for path, error in (
                ("/stall", TimeoutError),
                ("/trickle", TimeoutError),
                ("/cut", ConnectionError),
            ):
                started = time.monotonic()
                with self.subTest(path=path), self.assertRaises(error):
                    with urlopen(base + path, timeout=0.08) as result:
                        read_response(result, max_bytes=100, deadline=started + 0.2)
                self.assertLess(time.monotonic() - started, 1)
                with urlopen(base + "/ok", timeout=1) as result:
                    self.assertEqual(
                        read_response(result, max_bytes=100, deadline=time.monotonic() + 1), b"ok"
                    )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
