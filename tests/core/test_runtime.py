"""Tests for the launch-time helpers: FFmpeg DLL dir, running-instance check, launcher age."""

from __future__ import annotations

import http.server
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from annie.core import runtime


class TestFfmpegDllDir(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_override_wins(self) -> None:
        exe = self.tmp / "elsewhere" / "ffmpeg.exe"
        exe.parent.mkdir()
        exe.touch()
        self.assertEqual(runtime.ffmpeg_dll_dir(str(self.tmp), str(exe)), self.tmp)

    def test_falls_back_to_the_ffmpeg_on_path(self) -> None:
        exe = self.tmp / "ffmpeg.exe"
        exe.touch()
        self.assertEqual(runtime.ffmpeg_dll_dir(None, str(exe)), self.tmp)

    def test_missing_override_falls_back(self) -> None:
        exe = self.tmp / "ffmpeg.exe"
        exe.touch()
        self.assertEqual(runtime.ffmpeg_dll_dir(str(self.tmp / "gone"), str(exe)), self.tmp)

    def test_nothing_found(self) -> None:
        self.assertIsNone(runtime.ffmpeg_dll_dir(None, None))
        self.assertIsNone(runtime.ffmpeg_dll_dir("  ", None))

    @unittest.skipIf(sys.platform == "win32", "registers a real DLL directory on Windows")
    def test_ensure_is_a_no_op_off_windows(self) -> None:
        self.assertIsNone(runtime.ensure_windows_ffmpeg_dlls())


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves a fixed page whose body is set per server (``server.body``)."""

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = self.server.body  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Keep test output quiet."""


class TestAnnieRunning(unittest.TestCase):
    """A second launch must find the first Annie instead of crashing on a busy port."""

    def _serve(self, body: bytes) -> int:
        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        server.body = body  # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_free_port(self) -> None:
        port = self._free_port()
        self.assertFalse(runtime.port_in_use("127.0.0.1", port))
        self.assertFalse(runtime.annie_running("127.0.0.1", port))

    def test_annie_answers(self) -> None:
        port = self._serve(b"<html><head><title>Annie</title></head></html>")
        self.assertTrue(runtime.port_in_use("127.0.0.1", port))
        self.assertTrue(runtime.annie_running("127.0.0.1", port))

    def test_some_other_app_holds_the_port(self) -> None:
        port = self._serve(b"<html><head><title>Jupyter</title></head></html>")
        self.assertTrue(runtime.port_in_use("127.0.0.1", port))
        self.assertFalse(runtime.annie_running("127.0.0.1", port))

    def test_wildcard_host_is_probed_on_loopback(self) -> None:
        port = self._serve(b"<title>Annie</title>")
        self.assertTrue(runtime.annie_running("0.0.0.0", port))

    def test_app_url(self) -> None:
        self.assertEqual(runtime.app_url("0.0.0.0", 8080), "http://127.0.0.1:8080/")
        self.assertEqual(runtime.app_url("127.0.0.1", 8090), "http://127.0.0.1:8090/")


class TestLauncherOutdated(unittest.TestCase):
    def test_not_started_by_the_launcher(self) -> None:
        self.assertFalse(runtime.launcher_outdated(None))
        self.assertFalse(runtime.launcher_outdated(""))

    def test_current_launcher(self) -> None:
        self.assertFalse(runtime.launcher_outdated(str(runtime.REQUIRED_LAUNCHER_VERSION)))
        self.assertFalse(runtime.launcher_outdated(str(runtime.REQUIRED_LAUNCHER_VERSION + 1)))

    def test_older_launcher(self) -> None:
        self.assertTrue(runtime.launcher_outdated(str(runtime.REQUIRED_LAUNCHER_VERSION - 1)))

    def test_garbage_is_ignored(self) -> None:
        self.assertFalse(runtime.launcher_outdated("abc"))


if __name__ == "__main__":
    unittest.main()
