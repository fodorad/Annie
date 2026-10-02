"""Tests for the browser-side save helper (pure string logic; the dialogs are browser-gated)."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from annie.pages import download


class TestSanitizeFilename(unittest.TestCase):
    def _clean(self, name: str, ext: str = "csv") -> str:
        return download.sanitize_filename(name, extension=ext, default_stem="annie_events")

    def test_plain_name_gets_extension(self) -> None:
        self.assertEqual(self._clean("my events"), "my events.csv")

    def test_extension_not_doubled_and_case_insensitive(self) -> None:
        self.assertEqual(self._clean("out.csv"), "out.csv")
        self.assertEqual(self._clean("OUT.CSV"), "OUT.csv")

    def test_other_extension_is_kept_before_forced_one(self) -> None:
        self.assertEqual(self._clean("out.json", "csv"), "out.json.csv")

    def test_separators_and_reserved_chars_replaced(self) -> None:
        self.assertEqual(self._clean("a/b\\c:d*e?f"), "a_b_c_d_e_f.csv")
        self.assertNotIn("/", self._clean("../../etc/passwd"))

    def test_empty_or_dots_fall_back_to_default(self) -> None:
        self.assertEqual(self._clean(""), "annie_events.csv")
        self.assertEqual(self._clean("  .. "), "annie_events.csv")

    def test_long_names_are_truncated(self) -> None:
        self.assertLessEqual(len(self._clean("x" * 500)), 120 + len(".csv"))


class TestSaveFromUrl(unittest.TestCase):
    def test_fetches_then_opens_picker_with_fallback(self) -> None:
        js = download.save_from_url_js("a.csv", "/x/y", "text/csv")
        self.assertIn(json.dumps("/x/y"), js)
        self.assertLess(js.index("fetch("), js.index("showSaveFilePicker({"))
        self.assertIn("startIn: 'documents'", js)
        self.assertIn("suggestedName: name", js)
        self.assertIn("a.download", js)
        self.assertIn("AbortError", js)
        self.assertIn("204", js)

    def test_filename_and_mime_are_escaped_literals(self) -> None:
        js = download.save_from_url_js('we"ird.csv', "/x", "text/csv")
        self.assertIn(json.dumps('we"ird.csv'), js)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_script_is_valid_javascript(self) -> None:
        js = f"const handler = {download.save_from_url_js('a.csv', '/x', 'text/csv')};"
        result = subprocess.run(  # noqa: S603
            ["node", "--check", "-"],  # noqa: S607
            input=js,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
