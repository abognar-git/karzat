"""The README gate runs as part of the suite: a drifted number is a failing test.

If this fails after you changed code or config, either the README is wrong (fix it, or run
`python3 -m scripts.check_readme --sync` and read the diff) or a fragment registered in
scripts/check_readme.py no longer appears verbatim (re-register it). Both are the point.
"""

import contextlib
import io
import unittest

from scripts.check_readme import as_regex, build, main


class ReadmeGate(unittest.TestCase):
    def test_readme_numbers_match_code_config_and_tests(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main([])
        self.assertEqual(rc, 0, "\n" + out.getvalue())

    def test_every_registered_fragment_is_unique_and_present(self):
        from scripts.check_readme import README
        text = README.read_text(encoding="utf-8")
        for template, values in build():
            with self.subTest(template=template):
                hits = as_regex(template).findall(text)
                self.assertEqual(len(hits), 1)
                self.assertEqual(len(values), template.count("{"))

    def test_templates_survive_line_wrapping(self):
        rx = as_regex("TISZA {:,.0f},\nFidesz {:,.0f}")
        self.assertTrue(rx.search("… TISZA 141, Fidesz 44 …"))
        self.assertTrue(rx.search("… TISZA 141,\nFidesz 44 …"))
        self.assertTrue(rx.search("… TISZA 1,141,\n   Fidesz 44 …"))


if __name__ == "__main__":
    unittest.main()
