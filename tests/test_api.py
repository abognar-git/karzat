"""Offline tests for the W-API client scaffolding.

Nothing here touches the network. The XML fixtures below are SYNTHETIC — shaped after the
manual's section names (<szavazas idopont=...>, <tulajdonsagok>, <nev_szerint>), not after
real payloads. Once a token exists, replace them with trimmed real responses from data/raw/.
"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from karzat import api, xmlutil


class UrlBuilding(unittest.TestCase):
    def test_required_params_and_token(self):
        url = api.build_url("szavazas", {"p_szavdatum": "2026.09.15.09:37:07"}, "TOK")
        self.assertTrue(url.startswith(api.BASE_URL + "szavazas.cgi?"))
        self.assertIn("access_token=TOK", url)
        self.assertIn("p_szavdatum=2026.09.15.09%3A37%3A07", url)

    def test_missing_required_is_an_error(self):
        with self.assertRaises(api.ApiError):
            api.build_url("kepviselo", {}, "TOK")

    def test_unknown_param_is_an_error(self):
        with self.assertRaises(api.ApiError):
            api.build_url("kepviselok", {"p_bogus": 1}, "TOK")

    def test_unknown_service_is_an_error(self):
        with self.assertRaises(api.ApiError):
            api.build_url("titkos", {}, "TOK")

    def test_optional_empty_params_are_dropped(self):
        url = api.build_url("iromanyok", {"p_all": None}, "TOK")
        self.assertNotIn("p_all", url)

    def test_mask_token(self):
        self.assertEqual(api.mask_token("x.cgi?access_token=abc123&p_x=1"), "x.cgi?access_token=***&p_x=1")


class DatesAndSlugs(unittest.TestCase):
    def test_date_format_is_hungarian_dotted(self):
        self.assertEqual(xmlutil.fmt_date(date(2026, 9, 5)), "2026.09.05")
        self.assertEqual(xmlutil.parse_date("2026.09.05"), date(2026, 9, 5))

    def test_timestamp_roundtrip(self):
        ts = "2026.09.15.09:37:07"
        self.assertEqual(xmlutil.parse_ts(ts), datetime(2026, 9, 15, 9, 37, 7))
        self.assertEqual(xmlutil.fmt_ts(datetime(2026, 9, 15, 9, 37, 7)), ts)
        slug = xmlutil.ts_to_slug(ts)
        self.assertEqual(slug, "2026-09-15T09-37-07")
        self.assertEqual(xmlutil.slug_to_ts(slug), ts)

    def test_bad_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            xmlutil.parse_ts("2026-09-15 09:37")


SYNTHETIC_LIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<szavazasok>
  <szavazas idopont="2026.09.15.09:37:07">
    <tulajdonsagok><tipus>Z\xc3\xa1r\xc3\xb3szavaz\xc3\xa1s</tipus><eredmeny>Elfogadva</eredmeny></tulajdonsagok>
    <inditvanyok><inditvany izon="123">T/1234 valami</inditvany></inditvanyok>
  </szavazas>
  <szavazas idopont="2026.09.15.09:38:11">
    <tulajdonsagok><tipus>M\xc3\xb3dos\xc3\xadt\xc3\xb3</tipus><eredmeny>Elutas\xc3\xadtva</eredmeny></tulajdonsagok>
  </szavazas>
</szavazasok>
"""

SYNTHETIC_LATIN2 = "<?xml version='1.0' encoding='ISO-8859-2'?><k><nev>Ő Ű Á</nev></k>".encode("iso-8859-2")


class XmlConversion(unittest.TestCase):
    def test_to_dict_lists_and_attributes(self):
        root = xmlutil.parse_xml(SYNTHETIC_LIST)
        d = xmlutil.to_dict(root)
        votes = xmlutil.as_list(d["szavazas"])
        self.assertEqual(len(votes), 2)
        self.assertEqual(votes[0]["@idopont"], "2026.09.15.09:37:07")
        self.assertEqual(votes[0]["tulajdonsagok"]["tipus"], "Zárószavazás")
        self.assertEqual(votes[0]["inditvanyok"]["inditvany"]["@izon"], "123")
        self.assertEqual(votes[0]["inditvanyok"]["inditvany"]["#text"], "T/1234 valami")

    def test_declared_encoding_and_latin2_parse(self):
        self.assertEqual(xmlutil.declared_encoding(SYNTHETIC_LATIN2), "ISO-8859-2")
        d = xmlutil.to_dict(xmlutil.parse_xml(SYNTHETIC_LATIN2))
        self.assertEqual(d["nev"], "Ő Ű Á")

    def test_as_list_normalises(self):
        self.assertEqual(xmlutil.as_list(None), [])
        self.assertEqual(xmlutil.as_list("x"), ["x"])
        self.assertEqual(xmlutil.as_list(["x"]), ["x"])


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status


class Caching(unittest.TestCase):
    def test_second_fetch_is_served_from_disk_and_names_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = api.WebApi(token="TOK", cache_dir=Path(tmp), min_interval=0)
            with mock.patch.object(client.session, "get", return_value=_FakeResp(SYNTHETIC_LIST)) as g:
                a = client.fetch("szavazasok", p_datum_tol="2026.09.01", p_datum_ig="2026.09.30")
                b = client.fetch("szavazasok", p_datum_tol="2026.09.01", p_datum_ig="2026.09.30")
                self.assertEqual(g.call_count, 1)
            self.assertEqual(a, b)
            self.assertEqual(client.live_calls, 1)
            self.assertEqual(client.cache_hits, 1)
            path = client.cache_path("szavazasok", {"p_datum_tol": "2026.09.01", "p_datum_ig": "2026.09.30"})
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "2026-09-01__2026-09-30.xml")
            self.assertEqual(client.cache_path("szavazas", {"p_szavdatum": "2026.09.15.09:37:07"}).name,
                             "2026-09-15T09-37-07.xml")

    def test_non_xml_response_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = api.WebApi(token="TOK", cache_dir=Path(tmp), min_interval=0)
            with mock.patch.object(client.session, "get", return_value=_FakeResp(b"<html>bad token</html>")):
                # HTML *does* start with '<' — the client only rejects non-markup; the probe command
                # is what tells a human that the root tag is <html>. Simulate a bare error string:
                pass
            with mock.patch.object(client.session, "get", return_value=_FakeResp(b"ERROR: invalid token")):
                with self.assertRaises(api.ApiError):
                    client.fetch("kepviselok")
            self.assertFalse(client.cache_path("kepviselok", {}).exists())

    def test_missing_token_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                client = api.WebApi(token=None, cache_dir=Path(tmp))
                with self.assertRaises(api.MissingToken):
                    client.fetch("kepviselok")


if __name__ == "__main__":
    unittest.main()
