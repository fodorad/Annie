"""Tests for the Windows launcher's pure logic (installer/windows/launcher.pyw).

The launcher is a standalone, stdlib-only script run by the bundled Python, so it is
loaded here from its file. Only the parts that need no window and no install folder
are tested: the package-change list, uv's step lines and the progress bar's model.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "installer" / "windows" / "launcher.pyw"


def _load():  # noqa: ANN202 - a module
    loader = importlib.machinery.SourceFileLoader("annie_launcher", str(_PATH))
    spec = importlib.util.spec_from_loader("annie_launcher", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


launcher = _load()


class TestPackageChanges(unittest.TestCase):
    def test_update_new_and_removed(self) -> None:
        dry_run = [
            "Using Python 3.12.15 environment at: env",
            "Resolved 98 packages in 1.2s",
            "Would download 2 packages",
            "Would uninstall 2 packages",
            "Would install 3 packages",
            " - annie==1.13.1",
            " + annie==1.13.2",
            " - six==1.16.0",
            " + torch==2.14.2",
            " + typing-extensions==4.16.0",
        ]
        self.assertEqual(
            launcher.package_changes(dry_run),
            [
                "annie 1.13.1 → 1.13.2",
                "torch 2.14.2 (new)",
                "typing-extensions 4.16.0 (new)",
                "six 1.16.0 (removed)",
            ],
        )

    def test_nothing_to_change(self) -> None:
        self.assertEqual(launcher.package_changes(["Resolved 98 packages in 0.4s"]), [])

    def test_local_wheel_versions(self) -> None:
        line = " + annie==1.13.2.dev3+g1b43da0 (from file:///C:/dist/annie.whl)"
        self.assertEqual(launcher.package_changes([line]), ["annie 1.13.2.dev3+g1b43da0 (new)"])

    def test_a_local_wheel_in_a_dry_run(self) -> None:
        dry_run = [
            " - annie==1.12.0",
            " + annie @ file:///C:/Users/me/dist/annie-1.13.2.dev0+g3bd1910-py3-none-any.whl",
            " + pystray==0.19.5",
        ]
        self.assertEqual(
            launcher.package_changes(dry_run),
            ["annie 1.12.0 → 1.13.2.dev0+g3bd1910", "pystray 0.19.5 (new)"],
        )

    def test_a_url_that_is_not_a_wheel(self) -> None:
        line = " + annie @ git+https://github.com/fodorad/Annie"
        self.assertEqual(launcher.package_changes([line]), ["annie local build (new)"])


class TestUvStep(unittest.TestCase):
    def test_summary_lines_start_the_next_step(self) -> None:
        self.assertEqual(launcher.uv_step("Resolved 98 packages in 1.2s"), (True, "download"))
        self.assertEqual(launcher.uv_step("Prepared 3 packages in 41.0s"), (True, "install"))
        self.assertEqual(launcher.uv_step("Installed 3 packages in 2.1s"), (True, None))

    def test_other_lines(self) -> None:
        self.assertEqual(launcher.uv_step(" + annie==1.13.2"), (False, None))
        self.assertEqual(launcher.uv_step("Using Python 3.12.15"), (False, None))


class TestProgress(unittest.TestCase):
    def test_a_step_eases_toward_its_end_but_never_reaches_it(self) -> None:
        progress = launcher.Progress(launcher.QUICK_START)
        progress.begin("check", 0.0)
        early, later = progress.at(0.5), progress.at(60.0)
        self.assertGreater(early, 0.0)
        self.assertLess(early, later)
        self.assertLess(later, 0.15)  # the check's slice ends at 0.15

    def test_the_next_step_starts_at_its_slice(self) -> None:
        progress = launcher.Progress(launcher.QUICK_START)
        progress.begin("check", 0.0)
        progress.begin("start", 0.1)
        self.assertAlmostEqual(progress.at(0.1), 0.15)
        self.assertLess(progress.at(1000.0), 1.0)
        progress.finish()
        self.assertEqual(progress.at(1001.0), 1.0)

    def test_never_moves_backwards(self) -> None:
        progress = launcher.Progress(launcher.QUICK_START)
        progress.begin("check", 0.0)
        reached = progress.at(100.0)
        # The start turns out to need an update: more, smaller steps.
        progress.plan = launcher.UPDATE_START
        progress.begin("resolve", 100.0)
        self.assertGreaterEqual(progress.at(100.0), reached)
        values = [progress.at(100.0 + t) for t in range(0, 50, 5)]
        self.assertEqual(values, sorted(values))

    def test_an_unknown_step_holds_still(self) -> None:
        progress = launcher.Progress(launcher.QUICK_START)
        progress.begin("check", 0.0)
        before = progress.at(5.0)
        progress.begin("download", 5.0)  # not part of a quick start
        self.assertAlmostEqual(progress.at(500.0), max(before, progress.at(5.0)))

    def test_a_first_install_eases_slower(self) -> None:
        quick = launcher.Progress(launcher.FIRST_START)
        slow = launcher.Progress(launcher.FIRST_START, slow=True)
        for progress in (quick, slow):
            progress.begin("download", 0.0)
        self.assertGreater(quick.at(30.0), slow.at(30.0))

    def test_every_plan_ends_full_and_increases(self) -> None:
        for plan in (
            launcher.QUICK_START,
            launcher.UPDATE_START,
            launcher.FIRST_START,
            launcher.SETUP,
        ):
            ends = [end for _step, end in plan]
            self.assertEqual(ends, sorted(ends))
            self.assertEqual(ends[-1], 1.0)


def _log_to_a_temp_file(case: unittest.TestCase) -> None:
    """Point the launcher's install.log at a temp file (keeps it out of the repo)."""
    saved = launcher.LOG
    launcher.LOG = Path(tempfile.mkdtemp()) / "install.log"
    case.addCleanup(setattr, launcher, "LOG", saved)


class TestReporter(unittest.TestCase):
    def setUp(self) -> None:
        _log_to_a_temp_file(self)

    def test_events_reach_the_window_in_order(self) -> None:
        reporter = launcher.Reporter()
        reporter.status("Checking for updates...")
        reporter.step("download")
        reporter.advance(0.5)
        reporter.done()
        kinds = [reporter.events.get_nowait()[0] for _ in range(4)]
        self.assertEqual(kinds, ["status", "step", "advance", "done"])


class TestDownloads(unittest.TestCase):
    """uv announces each large download with its size, and says when it is done."""

    def test_notices(self) -> None:
        self.assertEqual(
            launcher.uv_download("Downloading torch (241.5MiB)"),
            ("start", "torch", int(241.5 * 1024**2)),
        )
        self.assertEqual(
            launcher.uv_download("Downloading pillow (834.0KiB)"), ("start", "pillow", 854016)
        )
        self.assertEqual(launcher.uv_download(" Downloaded torch"), ("done", "torch", 0))
        self.assertIsNone(launcher.uv_download("Prepared 3 packages in 2.1s"))
        self.assertIsNone(launcher.uv_download(" + torch==2.14.1"))

    def test_progress_is_weighted_by_size(self) -> None:
        downloads = launcher.Downloads()
        for line in ("Downloading torch (300.0MiB)", "Downloading numpy (100.0MiB)"):
            self.assertTrue(downloads.feed(line))
        self.assertEqual(downloads.fraction(), 0.0)
        downloads.feed(" Downloaded numpy")
        self.assertAlmostEqual(downloads.fraction(), 0.25)
        self.assertEqual(downloads.describe(), "Downloading: 100 of 400 MB")
        downloads.feed(" Downloaded torch")
        self.assertEqual(downloads.fraction(), 1.0)
        self.assertFalse(downloads.feed("Installed 2 packages in 3s"))

    def test_nothing_announced(self) -> None:
        self.assertEqual(launcher.Downloads().fraction(), 0.0)


class TestAdvance(unittest.TestCase):
    def test_a_known_fraction_moves_the_bar_within_the_step(self) -> None:
        progress = launcher.Progress(launcher.SETUP)
        progress.begin("download", 0.0)  # the download slice is 0.12..0.88
        progress.advance(0.5, 1.0)
        self.assertGreaterEqual(progress.at(1.0), 0.12 + 0.76 * 0.5 - 1e-9)
        self.assertLess(progress.at(1.0), 0.88)

    def test_never_backwards_and_never_past_the_step(self) -> None:
        progress = launcher.Progress(launcher.SETUP)
        progress.begin("download", 0.0)
        progress.advance(0.8, 1.0)
        reached = progress.at(1.0)
        progress.advance(0.2, 2.0)  # more downloads were announced: the share dropped
        self.assertGreaterEqual(progress.at(2.0), reached)
        progress.advance(5.0, 3.0)
        self.assertLessEqual(progress.at(3.0), 0.88)

    def test_outside_a_step_nothing_happens(self) -> None:
        progress = launcher.Progress(launcher.SETUP)
        progress.advance(0.5, 1.0)
        self.assertEqual(progress.at(1.0), 0.0)


class TestStreamReporter(unittest.TestCase):
    """Setup's window reads the launcher's stdout: @status, @progress and log lines."""

    def setUp(self) -> None:
        _log_to_a_temp_file(self)
        self.lines: list[str] = []

    def _reporter(self) -> object:
        return launcher.StreamReporter(launcher.SETUP, out=self.lines.append)

    def test_protocol(self) -> None:
        ui = self._reporter()
        ui.step("download")
        ui.status("Updating Annie 1.15.0 → 1.15.1…")
        ui.detail("    torch 2.14.1 → 2.14.2")
        ui.done()
        self.assertIn("@status Updating Annie 1.15.0 -> 1.15.1...", self.lines)
        self.assertIn("    torch 2.14.1 -> 2.14.2", self.lines)
        self.assertEqual(self.lines[-1], "@progress 1000")
        progress = [int(x.split()[1]) for x in self.lines if x.startswith("@progress ")]
        self.assertEqual(progress, sorted(progress))
        for line in self.lines:
            line.encode("ascii")  # setup reads plain ASCII

    def test_failure(self) -> None:
        ui = self._reporter()
        ui.fail("uv pip failed (exit code 2)")
        self.assertIn("@status uv pip failed (exit code 2)", self.lines)
        self.assertIn("ERROR: uv pip failed (exit code 2)", self.lines)

    def test_heartbeat_sends_only_changes(self) -> None:
        ui = self._reporter()
        ui.beat(0.0)
        ui.beat(0.0)
        self.assertEqual([x for x in self.lines if x.startswith("@progress")], ["@progress 0"])

    def test_run_streaming_reports_the_outcome(self) -> None:
        def broken(ui: object) -> None:
            raise RuntimeError("no network")

        ok = launcher.run_streaming(launcher.SETUP, lambda ui: None, self._reporter())
        self.assertTrue(ok)
        self.assertEqual(self.lines[-1], "@progress 1000")
        self.assertFalse(launcher.run_streaming(launcher.SETUP, broken, self._reporter()))
        self.assertIn("ERROR: no network", self.lines)


if __name__ == "__main__":
    unittest.main()
