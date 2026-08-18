"""Golden test: pinned fingerprints of how this code reads each fixture payload.

A failure here is not necessarily a bug — it means the reading of a payload changed
(xmlutil.to_dict today; the normaliser once it exists). That is exactly what you want to be
told when you touch the parser, because every derived table, every README number and every
published page depends on that reading being stable.

If the change was intended, regenerate deliberately:

    python3 -m karzat fingerprint tests/fixtures/*.xml

and paste the table into GOLDEN. Do it deliberately, not reflexively — and say in the commit
message which fixtures moved and why.

Two further rules, both enforced below: every file in tests/fixtures/ must be registered
(no fixture is pinned by accident or forgotten), and every registered name must exist.
"""

import unittest
from pathlib import Path

from karzat.fingerprint import digest, fingerprint_file

FIXTURES = Path(__file__).parent / "fixtures"

# real_* = trimmed W-API responses captured 2026-08-18 (see tests/fixtures/README.md);
# synthetic_kepviselok_latin2.xml stays only to exercise the ISO-8859-2 path the API does not use.
GOLDEN = {
    "real_iromany_71.xml":                     "fb700e8dff587bac",
    "real_iromanyok_head3.xml":                "ebe00be955b15b82",
    "real_kepviselo_a011_trimmed.xml":         "4322a43dbccb5b8a",
    "real_kepviselok_head5.xml":               "fe5d4f1f3b6cb092",
    "real_szavazas_2026-05-09T10-45-47.xml":   "9bd69cb450e23a85",
    "real_szavazas_2026-05-09T11-50-00.xml":   "f54b3847abf049a7",
    "real_szavazas_2026-05-26T16-31-45.xml":   "c3916aa8b91162a2",
    "real_szavazas_2026-06-15T17-20-04.xml":   "5216b4de1165d62c",
    "real_szavazasok_2026-05_head3.xml":       "7e164703fc8d7645",
    "real_ulesnap_43.xml":                     "41067c37c03be90c",
    "synthetic_kepviselok_latin2.xml":         "2ca340cdfa39b941",
}


class TestGolden(unittest.TestCase):
    def test_pinned_fixtures_are_unchanged(self):
        actual = {name: fingerprint_file(FIXTURES / name) for name in GOLDEN}
        self.assertEqual(actual, GOLDEN, "payload reading changed — see this module's docstring")

    def test_every_fixture_is_registered_and_every_registration_exists(self):
        on_disk = {p.name for p in FIXTURES.glob("*.xml")}
        self.assertEqual(on_disk, set(GOLDEN),
                         f"unregistered: {sorted(on_disk - set(GOLDEN))}; missing: {sorted(set(GOLDEN) - on_disk)}")

    def test_digest_is_canonical(self):
        # key order and whitespace do not matter; values do
        self.assertEqual(digest({"a": 1, "b": [1, 2]}), digest({"b": [1, 2], "a": 1}))
        self.assertNotEqual(digest({"a": 1}), digest({"a": 2}))
        self.assertEqual(len(digest({"x": "Ő"})), 16)


if __name__ == "__main__":
    unittest.main()
