"""Offline tests for the W-API client scaffolding.

Nothing here touches the network. Fixtures under tests/fixtures/real_* are trimmed real W-API
responses captured on 2026-08-18; the one synthetic_* file exercises the ISO-8859-2 path the
real API does not use. tests/test_golden.py pins how every fixture is read.
"""

import tempfile
import json
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from karzat import api, xmlutil

FIXTURES = Path(__file__).parent / "fixtures"
REAL_LIST = (FIXTURES / "real_szavazasok_2026-05_head3.xml").read_bytes()
SYNTHETIC_LATIN2 = (FIXTURES / "synthetic_kepviselok_latin2.xml").read_bytes()


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


class XmlConversion(unittest.TestCase):
    def test_to_dict_lists_and_attributes(self):
        root = xmlutil.parse_xml(REAL_LIST)
        d = xmlutil.to_dict(root)
        votes = xmlutil.as_list(d["szavazas"])
        self.assertEqual(len(votes), 3)
        self.assertRegex(votes[0]["@idopont"], r"^\d{4}\.\d{2}\.\d{2}\.\d{2}:\d{2}:\d{2}$")
        props = xmlutil.as_list(votes[0]["tulajdonsagok"]["tulajdonsag"])
        self.assertEqual(props[0]["@nev"], "Szavazási mód")           # attributes -> '@' keys, repeated tags -> lists
        self.assertIn("inditvanyok", votes[0])

    def test_declared_encoding_and_latin2_parse(self):
        self.assertEqual(xmlutil.declared_encoding(SYNTHETIC_LATIN2), "ISO-8859-2")
        d = xmlutil.to_dict(xmlutil.parse_xml(SYNTHETIC_LATIN2))
        self.assertEqual(d["kepviselo"]["nev"], "Ő Ű Á Példa")
        self.assertEqual(d["kepviselo"]["@p_azon"], "z004")

    def test_as_list_normalises(self):
        self.assertEqual(xmlutil.as_list(None), [])
        self.assertEqual(xmlutil.as_list("x"), ["x"])
        self.assertEqual(xmlutil.as_list(["x"]), ["x"])

    def test_source_defects_are_repaired(self):
        # 1990: an unescaped quote inside an attribute; 2003: a bare '&' ("K&H Equities Rt.").
        quote = b'<a><t nev="x" ertek="hogy "kell-e" tovabb"/></a>'
        self.assertEqual(xmlutil.parse_xml(quote).find("t").get("ertek"), 'hogy "kell-e" tovabb')
        amp = b'<a><t nev="x" ertek="a K&amp;H Equities Rt.-n\xc3\xa9l K&H tov&#225;bb"/></a>'
        self.assertEqual(xmlutil.parse_xml(amp).find("t").get("ertek"), "a K&H Equities Rt.-nél K&H tovább")
        # real entities are left alone; a document that is fine is untouched
        self.assertEqual(xmlutil.repair_bare_ampersands(b"&amp; &#225; &#x41; & &&x"), b"&amp; &#225; &#x41; &amp; &amp;&amp;x")
        good = b"<a><t ertek='1 &amp; 2'/></a>"
        self.assertEqual(xmlutil.parse_xml(good).find("t").get("ertek"), "1 & 2")


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status


class Caching(unittest.TestCase):
    def test_second_fetch_is_served_from_disk_and_names_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = api.WebApi(token="TOK", cache_dir=Path(tmp), min_interval=0)
            with mock.patch.object(client.session, "get", return_value=_FakeResp(REAL_LIST)) as g:
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
            # an HTML error page starts with '<' too: it must be refused, not cached and later parsed as data
            for body in (b"<!DOCTYPE html>\n<html><body>Invalid access token TOK</body></html>", b"<html>bad token</html>",
                         b"ERROR: invalid token"):
                with mock.patch.object(client.session, "get", return_value=_FakeResp(body)):
                    with self.assertRaises(api.ApiError) as cm:
                        client.fetch("kepviselok")
                    self.assertNotIn("TOK", str(cm.exception))            # the token never reaches a log line
                self.assertFalse(client.cache_path("kepviselok", {}).exists())

    def test_transport_errors_are_masked_retried_and_eventually_stop(self):
        import requests
        with tempfile.TemporaryDirectory() as tmp:
            client = api.WebApi(token="TOK", cache_dir=Path(tmp), min_interval=0, retries=1)
            boom = requests.ConnectionError("Max retries exceeded with url: /cgi-bin/x.cgi?access_token=TOK&p=1")
            with mock.patch.object(client.session, "get", side_effect=boom) as g, mock.patch("karzat.api.time.sleep"):
                with self.assertRaises(api.ApiError) as cm:
                    client.fetch("kepviselok")
                self.assertEqual(g.call_count, 2)                          # one retry
            self.assertNotIn("TOK", str(cm.exception))
            self.assertIn("ConnectionError", str(cm.exception))
            # a 503 then a 200: served after the retry, and the failure counter is reset
            with mock.patch.object(client.session, "get", side_effect=[_FakeResp(b"x", 503), _FakeResp(REAL_LIST)]), mock.patch("karzat.api.time.sleep"):
                self.assertEqual(client.fetch("szavazasok", p_datum_tol="2026.09.01", p_datum_ig="2026.09.30"), REAL_LIST)
            self.assertEqual(client.consecutive_failures, 0)
            # a 404 is not retried; five failures in a row open the circuit
            with mock.patch.object(client.session, "get", return_value=_FakeResp(b"", 404)) as g:
                for _ in range(5):
                    with self.assertRaises(api.ApiError):
                        client.fetch("kepviselo", p_azon=f"x{_}")
                self.assertEqual(g.call_count, 5)
                with self.assertRaises(api.ApiError) as cm:
                    client.fetch("kepviselo", p_azon="y")
                self.assertEqual(g.call_count, 5)                          # no sixth request
            self.assertIn("in a row", str(cm.exception))

    def test_missing_token_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {}, clear=True):
                client = api.WebApi(token=None, cache_dir=Path(tmp))
                with self.assertRaises(api.MissingToken):
                    client.fetch("kepviselok")


if __name__ == "__main__":
    unittest.main()


class SupersededPayloads(unittest.TestCase):
    """A refresh must not be able to lose what the source used to say.

    The cache is this project's only evidence of what the API answered on the day it was asked. `fetch` used to
    overwrite it, so if parliament.hu amended a record — it publishes no changelog and the payloads carry no
    version — the old bytes vanished and a published number could move with nothing anywhere to explain it. That
    is the failure this whole project is built to prevent, arriving through the one door nobody was watching."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.api = api.WebApi(token="secrettoken", cache_dir=self.tmp, min_interval=0)
        self.kw = {"p_szavdatum": "2020.01.01.10:00:00"}

    def _fetch(self, body: bytes, refresh: bool = False) -> bytes:
        class R:
            def __init__(self, b):
                self.content, self.status_code = b, 200
        with mock.patch.object(self.api.session, "get", return_value=R(body)):
            return self.api.fetch("szavazas", refresh=refresh, **self.kw)

    def test_a_rewritten_record_keeps_its_predecessor(self):
        first = b"<szavazas><eredmeny>Elfogadva</eredmeny></szavazas>"
        second = b"<szavazas><eredmeny>Elutasitva</eredmeny></szavazas>"
        self._fetch(first)
        self.assertEqual(self._fetch(second, refresh=True), second)     # the cache moves on…
        kept = list((self.tmp / ".superseded" / "szavazas").glob("*.xml"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), first)                    # …and the old answer survives

    def test_the_change_is_logged_without_the_token(self):
        self._fetch(b"<szavazas><a/></szavazas>")
        self._fetch(b"<szavazas><b/></szavazas>", refresh=True)
        line = (self.tmp / ".superseded" / "changes.jsonl").read_text(encoding="utf-8").strip()
        self.assertNotIn("secrettoken", line, "the change log leaked the API token")
        rec = json.loads(line)
        self.assertEqual(rec["service"], "szavazas")
        self.assertEqual(rec["params"], self.kw)
        self.assertNotEqual(rec["was"]["sha256"], rec["now"]["sha256"])

    def test_an_unchanged_refresh_records_nothing(self):
        body = b"<szavazas><a/></szavazas>"
        self._fetch(body)
        self._fetch(body, refresh=True)
        self.assertFalse((self.tmp / ".superseded").exists(), "an identical refresh was logged as a change")

    def test_the_watcher_asks_for_exactly_the_record_it_holds(self):
        """The sweep turns a cache filename back into the parameter that produced it. If that round trip is not
        exact the watcher re-fetches the wrong record, caches it under a new name, and reports nothing wrong."""
        from scripts.watch_source import _param_from_key
        for stem in ("2019-06-04T13-12-50", "2026-05-09T11-42-46", "1998-12-15T09-05-00"):
            self.assertEqual(api.cache_key("szavazas", {"p_szavdatum": _param_from_key(stem)}), stem)
