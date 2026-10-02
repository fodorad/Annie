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


class TestSaveScript(unittest.TestCase):
    def test_contains_picker_and_fallback(self) -> None:
        js = download.save_script("a.csv", "x", "text/csv")
        self.assertIn("showSaveFilePicker", js)
        self.assertIn("startIn: 'documents'", js)
        self.assertIn("a.download", js)
        self.assertIn("AbortError", js)

    def test_content_is_escaped(self) -> None:
        text = 'he said "hi"\nline2 </script><b>'
        js = download.save_script("a.csv", text, "text/csv")
        self.assertIn(json.dumps("a.csv"), js)
        self.assertNotIn("</script>", js)
        self.assertNotIn('he said "hi"', js)  # quotes are backslash-escaped

    def test_name_input_is_wired_when_given(self) -> None:
        self.assertIn(
            '"my-id"', download.save_script("a.csv", "x", "text/csv", name_input_id="my-id")
        )

    def test_no_raw_control_characters(self) -> None:
        js = download.save_script("a.csv", "x", "text/csv", name_input_id="i")
        self.assertFalse([c for c in js if ord(c) < 32 and c not in "\n \t"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_script_is_valid_javascript(self) -> None:
        js = download.save_script("a.csv", 'q"\n</script>', "text/csv", name_input_id="i")
        result = subprocess.run(  # noqa: S603
            ["node", "--check", "-"],
            input=js,
            text=True,
            capture_output=True,
            check=False,  # noqa: S607
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
