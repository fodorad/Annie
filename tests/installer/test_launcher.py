"""Tests for the Windows launcher's pure logic (installer/windows/launcher.pyw).

The launcher is a standalone, stdlib-only script run by the bundled Python, so it is
loaded here from its file. Only the parts that need no window and no install folder
are tested: the package-change list, uv's step lines and the progress bar's model.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
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


class TestReporter(unittest.TestCase):
    def test_events_reach_the_window_in_order(self) -> None:
        reporter = launcher.Reporter()
        reporter.heading("Updating Annie")
        reporter.step("download")
        reporter.done()
        kinds = [reporter.events.get_nowait()[0] for _ in range(3)]
        self.assertEqual(kinds, ["heading", "step", "done"])


if __name__ == "__main__":
    unittest.main()
