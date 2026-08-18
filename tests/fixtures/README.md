# Fixtures

Payloads the tests and the golden fingerprints run against.

- `synthetic_*.xml` — invented by hand from the manual's section names before any real
  response was seen. They exercise the parser and the golden mechanism; **they do not
  describe the real API.** Delete each one the day a real counterpart lands.
- `real_<service>_<key>.xml` — trimmed copies of actual W-API responses from `data/raw/`
  (e.g. `real_szavazas_2026-09-15T09-37-07.xml`). Trim to the smallest payload that still
  shows every element the code reads; keep the XML declaration so the encoding path is
  tested; never include a URL with the access token (payloads do not carry it, but check).

Every file here must be registered in `tests/test_golden.py`; the test fails on an
unregistered fixture as well as on a changed digest, so nothing is pinned by accident.
