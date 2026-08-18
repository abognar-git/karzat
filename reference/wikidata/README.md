# Wikidata snapshot — members of the Országgyűlés, cycle 43

`members_ckl43.json` is what Wikidata said on the day recorded in its `retrieved_at` field
(first pull: 2026-08-18), produced by `python3 -m scripts.pull_wikidata` with the SPARQL
query stored inside the file. It is a snapshot, not a live mirror; re-pull deliberately.

## What is in it

One record per (person, P39 statement) for "member of the National Assembly of Hungary"
(Q17590876) whose statement started on/after 2026-04-01 — ended statements included, so
resignations and their replacements are visible. Per record: QID, Hungarian/English label,
**P4966 Hungarian National Assembly ID** (its formatter URL ends in `ogy_kpv.kepv_adat?p_azon=$1`,
i.e. it is the W-API's `p_azon`), the **P4100 parliamentary group** qualifier with QID,
start/end, P768 constituency (only egyéni MPs have one — 106, which is the number of
single-member districts), P102 party (see caveat), birth date, sex, Commons image name,
thumbnail URL and the image's Commons licence and author.

## Caveats, read before use

- **Group comes from P4100, not P102.** Party membership (P102) lags reality: it still says
  Jobbik for several Mi Hazánk MPs and SZDSZ for two others. The parliamentary-group qualifier
  on the statement is what the chamber diagram should use.
- **Community-maintained.** Everything here is to be verified against `kepviselok.cgi` /
  `<kepvcsop-tagsagok>` before it is published; the schema treats Wikidata as an identity
  spine and a cross-check, not as the source of votes or membership.
- **Coverage on first pull:** 199 current members, all with a group; 197 with a P4966 id
  (the two without are the replacements seated on 2026-08-10); 49 with a Commons portrait.
  Portraits are few and their licences vary (CC BY-SA, CC BY, CC0, public domain, but also
  "European Parliament" and bare "Attribution") — treat licence as per-file data, never as
  a blanket permission.
- **Wikidata is CC0**; the images are not. Attribute per file.

## Regenerating

```bash
python3 -m scripts.pull_wikidata                       # cycle 43, with licences
python3 -m scripts.pull_wikidata --since 2022-05-01 --out reference/wikidata/members_ckl42.json
```

`tests/test_wikidata.py` asserts the snapshot's summary matches a recomputation and that
`config/factions.yml` seat counts equal its per-group totals; `scripts/check_readme.py`
gates the README's numbers on the same file. A re-pull that changes counts fails both until
config and README are updated — on purpose.
