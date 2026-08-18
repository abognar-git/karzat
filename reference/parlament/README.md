# parlament.hu reference data

- `patko_seats.json` — the chamber's 274 seats with their outlines, fetched 2026-08-18 from the
  query behind the seat map on every MP page on parlament.hu
  (`felicitas/api/query/select/patko/patko-queries/kepviselo-helye-apatkoban`, POST body = the MP id
  as a JSON string, header `X-Query-ID`; the response marks the MP's own seat with `ottUl`, dropped
  here). Fields: `szektor`, `sor`, `szek` (the same triple `kepviselo.cgi` prints as `<ulohely>`),
  `szektipus` (`null` for an ordinary seat, `p` for 21 seats, `s` for one — meaning not stated by
  the source; the `p` seats sit at the outer ends of sectors 1–2 / 5–6 and none is occupied by an
  MP), and `svgpath`, the seat's outline in parlament.hu's own coordinate space. This is the floor
  plan `karzat/seating.py` lays MPs on; nothing about the room's geometry is estimated any more.
- `kepviselok_1990_2026.json` — every MP since 1990 (1,612 people) as parlament.hu's Képviselők list
  query returns them (`kepviselo-query-provider/kepviselo-lista-idopontban`, with the period filter
  off): p_azon, the API's own spelling of the name, and their faction stints / offices (undated).
  Fetched 2026-08-18. This is what tells the name resolver which ids exist for a name and which
  `kepviselo.cgi` records to fetch for the dated faction history.
