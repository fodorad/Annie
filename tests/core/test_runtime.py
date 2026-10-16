"""Tests for the launch-time helpers: FFmpeg DLL dir, running-instance check, launcher age."""

from __future__ import annotations

import ast
import contextlib
import http.server
import socket
import ssl
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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

    def test_something_not_speaking_http_holds_the_port(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        self.addCleanup(listener.close)

        def hang_up() -> None:
            with contextlib.suppress(OSError):
                conn, _ = listener.accept()  # the port_in_use probe
                conn.close()
                conn, _ = listener.accept()  # the HTTP request
                conn.close()

        threading.Thread(target=hang_up, daemon=True).start()
        self.assertFalse(runtime.annie_running("127.0.0.1", listener.getsockname()[1]))

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


class TestVersions(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(runtime.parse_version("1.12.0"), (1, 12, 0))
        self.assertEqual(runtime.parse_version("v1.13.0.dev2+gabc"), (1, 13, 0))
        self.assertEqual(runtime.parse_version("2.0rc1"), (2, 0))
        self.assertIsNone(runtime.parse_version("unknown"))

    def test_newer_release(self) -> None:
        self.assertTrue(runtime.is_newer("1.12.1", "1.12.0"))
        self.assertTrue(runtime.is_newer("1.13.0", "1.12.9"))
        self.assertTrue(runtime.is_newer("1.10.0", "1.9.0"))  # numeric, not text

    def test_same_or_older_is_not_newer(self) -> None:
        self.assertFalse(runtime.is_newer("1.12.0", "1.12.0"))
        self.assertFalse(runtime.is_newer("1.12", "1.12.0"))
        self.assertFalse(runtime.is_newer("1.11.0", "1.12.0"))
        # A dev build of an unreleased version is not behind that version's release.
        self.assertFalse(runtime.is_newer("1.13.0", "1.13.0.dev4+g1a2b3c"))

    def test_garbage_never_triggers_an_update(self) -> None:
        self.assertFalse(runtime.is_newer("", "1.0.0"))
        self.assertFalse(runtime.is_newer("2.0.0", "unknown"))


class TestHiddenConsole(unittest.TestCase):
    """ffmpeg/ffprobe started by the windowless (pythonw) Annie must not flash a console."""

    def test_windows_hides_the_console_window(self) -> None:
        self.assertEqual(runtime.hidden_console("win32"), 0x08000000)

    def test_other_platforms_need_nothing(self) -> None:
        self.assertEqual(runtime.hidden_console("linux"), 0)
        self.assertEqual(runtime.hidden_console("darwin"), 0)

    def test_every_console_program_annie_starts_is_hidden(self) -> None:
        """Each subprocess call passes ``creationflags=hidden_console()``, except windowed ones."""
        windowed = {"desktop.py", "reveal.py"}  # the launcher (pythonw) and Explorer
        package = Path(runtime.__file__).resolve().parents[1]
        unhidden = []
        for source in sorted(package.rglob("*.py")):
            if source.name in windowed:
                continue
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}
                ):
                    continue
                hidden = any(
                    kw.arg == "creationflags"
                    and isinstance(kw.value, ast.Call)
                    and getattr(kw.value.func, "id", "") == "hidden_console"
                    for kw in node.keywords
                )
                if not hidden:
                    unhidden.append(f"{source.relative_to(package)}:{node.lineno}")
        self.assertEqual(
            unhidden, [], "pass creationflags=hidden_console() to these subprocess calls"
        )


class TestDecoderProblem(unittest.TestCase):
    """The message shown when the frame decoder cannot load."""

    _NO_FFMPEG = RuntimeError(
        "Could not load libtorchcodec. Likely causes:\n  1. FFmpeg is not properly installed"
    )

    def test_missing_ffmpeg_in_the_installed_app(self) -> None:
        message = runtime.decoder_problem(self._NO_FFMPEG, "win32", installed=True)
        self.assertIn("FFmpeg's shared libraries were not found", message)
        self.assertIn(runtime.INSTALLER_URL, message)
        self.assertNotIn("\n", message)  # one line: the 150-line text stays in the details

    def test_missing_ffmpeg_from_source_on_windows(self) -> None:
        message = runtime.decoder_problem(self._NO_FFMPEG, "win32", installed=False)
        self.assertIn("winget install BtbN.FFmpeg.GPL.Shared", message)
        self.assertIn("new terminal", message)

    def test_missing_ffmpeg_elsewhere(self) -> None:
        message = runtime.decoder_problem(self._NO_FFMPEG, "linux", installed=False)
        self.assertIn("shared libraries", message)
        self.assertNotIn("winget", message)

    def test_installed_is_detected_from_the_launcher(self) -> None:
        with mock.patch.dict("os.environ", {"ANNIE_INSTALL_DIR": r"C:\Annie\App"}):
            self.assertIn(runtime.INSTALLER_URL, runtime.decoder_problem(self._NO_FFMPEG, "win32"))

    _DLL_ERROR = OSError(
        126,
        'Error loading "C:\\App\\env\\Lib\\site-packages\\torch\\lib\\c10.dll" or one of '
        "its dependencies.",
    )

    def test_missing_windows_dll_points_to_the_fix(self) -> None:
        message = runtime.decoder_problem(self._DLL_ERROR, "win32")
        self.assertIn("c10.dll", message)
        self.assertIn(runtime.INSTALLER_URL, message)
        self.assertIn(runtime.VC_REDIST_URL, message)

    def test_same_error_elsewhere_is_reported_plainly(self) -> None:
        message = runtime.decoder_problem(self._DLL_ERROR, "linux")
        self.assertIn("c10.dll", message)
        self.assertNotIn(runtime.VC_REDIST_URL, message)

    def test_import_error(self) -> None:
        message = runtime.decoder_problem(ImportError("No module named 'torch'"), "win32")
        self.assertIn("No module named 'torch'", message)
        self.assertNotIn(runtime.VC_REDIST_URL, message)

    def test_empty_message_names_the_exception(self) -> None:
        self.assertIn("ImportError", runtime.decoder_problem(ImportError(), "darwin"))


class TestTlsContext(unittest.TestCase):
    """Downloads verify certificates, with certifi's roots when certifi is installed."""

    def test_verifies_and_trusts_certifi(self) -> None:
        context = runtime._tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

    def test_without_certifi_still_verifies(self) -> None:
        with mock.patch.dict(sys.modules, {"certifi": None}):
            context = runtime._tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class TestAnnieFolder(unittest.TestCase):
    def setUp(self) -> None:
        from annie.core.config import Settings

        self.root = Path(tempfile.mkdtemp()) / "Annie"
        self.settings = Settings(annie_home=self.root / "Data")

    def test_installer_layout_opens_the_common_annie_folder(self) -> None:
        self.assertEqual(runtime.annie_folder(self.settings, self.root / "App"), self.root)

    def test_unrelated_locations_open_the_data_folder(self) -> None:
        elsewhere = self.root.parent / "Programs" / "Annie"
        self.assertEqual(runtime.annie_folder(self.settings, elsewhere), self.root / "Data")

    def test_without_the_installer_opens_the_data_folder(self) -> None:
        self.assertEqual(runtime.annie_folder(self.settings, None), self.root / "Data")


class TestExamples(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.archive = self.tmp / "annie-examples.zip"
        self.dest = self.tmp / "Data" / "examples"

    def _zip(self, entries: dict[str, str]) -> None:
        with zipfile.ZipFile(self.archive, "w") as zf:
            for name, text in entries.items():
                zf.writestr(name, text)

    def test_extracts_configs_and_media(self) -> None:
        self._zip({"config/mosei_mini.json": "{}", "example/mosei_mini/label.csv": "a\n"})
        self.assertFalse(runtime.examples_installed(self.dest))
        self.assertEqual(runtime.extract_examples(self.archive, self.dest), 2)
        self.assertTrue(runtime.examples_installed(self.dest))
        self.assertEqual((self.dest / "example" / "mosei_mini" / "label.csv").read_text(), "a\n")

    def test_backslash_names_from_windows_zippers(self) -> None:
        self._zip({"config\\fi_mini.json": "{}"})
        runtime.extract_examples(self.archive, self.dest)
        self.assertTrue((self.dest / "config" / "fi_mini.json").is_file())

    def test_ignores_other_folders_and_escapes(self) -> None:
        self._zip(
            {
                "config/x.json": "{}",
                "config/../../evil.txt": "no",
                "README.md": "no",
                "scripts/run.py": "no",
            }
        )
        self.assertEqual(runtime.extract_examples(self.archive, self.dest), 1)
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertFalse((self.tmp / "Data" / "evil.txt").exists())
        self.assertFalse((self.dest / "README.md").exists())
        self.assertFalse((self.dest / "scripts").exists())

    def test_archive_without_configs_is_rejected(self) -> None:
        self._zip({"example/only/video.mp4": "x"})
        with self.assertRaises(ValueError):
            runtime.extract_examples(self.archive, self.dest)

    def test_not_a_zip(self) -> None:
        self.archive.write_text("<html>404</html>")
        with self.assertRaises(zipfile.BadZipFile):
            runtime.extract_examples(self.archive, self.dest)

    def test_the_repo_examples_have_the_archive_layout(self) -> None:
        data = Path(__file__).resolve().parents[2] / "data"
        self.assertTrue(runtime.examples_installed(data))
        for folder in data.iterdir():
            if folder.name.startswith("."):  # OS junk (e.g. Finder's .DS_Store), never tracked
                continue
            self.assertIn(folder.name, runtime.EXAMPLE_FOLDERS)
