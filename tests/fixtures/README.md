# Fixtures

Payloads the tests and the golden fingerprints run against.

- `real_<service>_<key>.xml` — real W-API responses captured on 2026-08-18 (cycle 43, plus one
  cycle-42 roll call, `real_szavazas_ckl42_2025-05-20T10-20-49.xml`, kept whole because it carries
  the seventh position state "Ig.távol" and both absence states at once: igtav_db 10 = 6 + 4), trimmed
  where the whole payload is not the point (`_head5`, `_head3`, `_trimmed`) and kept whole
  where it is (the four `real_szavazas_*` roll calls, `real_ulesnap_43`, `real_iromany_71`).
  Trimming was done with ElementTree, so element order and content are the API's; only the
  XML declaration/serialisation is ours. Public parliamentary data; no token appears anywhere.
- `synthetic_kepviselok_latin2.xml` — invented; the API is UTF-8, this file exists so the
  ISO-8859-2 branch of the parser stays tested.

What the real ones cover: `kepviselok` (5 MPs incl. list and constituency mandates),
`kepviselo` (a011: seat, faction history, elections, motion stats), `szavazasok` (3 list
entries), `szavazas` × 4 (2/3-of-present rejected mentelmi vote; secret Speaker election;
quorum check with "Jelen, nem szav."; 16th Alaptörvény amendment, 2/3 of all, 135–50–6),
`ulesnap` (23 sitting days, tavaszi + nyári), `iromany` (T/71: kivételes eljárás, kihirdetve
as 2026. évi XV. törvény, MK 60), `iromanyok` (3 entries).

Every file here must be registered in `tests/test_golden.py`; the test fails on an
unregistered fixture as well as on a changed digest, so nothing is pinned by accident.
- `real_szoszolok_trimmed.xml` — the first two rows of `szoszolok.cgi` (12 nationality spokespersons on 2026-08-19): id, name, nationality, photo.
