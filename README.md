# karzat

**Every recorded vote of the Hungarian Parliament since 1990 — one page per vote, drawn
seat by seat, with the majority rule the House itself applied.**

**Live: [ogykarzat.hu](https://ogykarzat.hu)** — in Hungarian, because its audience is the
Hungarian public. This document is for the people who want to see how it is built.

![The karzat landing page: the chamber as it sits today, every member on their real seat](docs/media/landing.png)

## What you can see

**One vote, one page.** Every vote since 1990 has its own address: the seat chart, the
tally, the majority rule that applied, and the full roll call — sortable, filterable,
downloadable. The site recomputes every verdict from the raw positions and says so when it
disagrees with the source.
[→ a vote page](https://ogykarzat.hu/ckl43/szavazas/2026-07-28T10-35-33.html)

![One vote: seat chart with dissent rings, the tally, and the rule it needed](docs/media/vote.png)

**The chamber answers the cursor.** Point at any seat: who sits there, and what they have
done this cycle. The legend filters one faction. Everything works by keyboard too, and the
page is complete without JavaScript.
[→ ogykarzat.hu](https://ogykarzat.hu)

![Hovering seats on the landing chamber; a member card follows; a faction filter dims the rest](docs/media/inspector.gif)

**A career on one axis.** Every person who ever held a mandate since 1990 has a page:
mandates, factions, per-cycle voting record, committees, submissions.
[→ a career page](https://ogykarzat.hu/szemely/a011.html)

![A career page: six cycles, per-cycle roll-call record, faction history](docs/media/career.png)

**Charts that answer precisely.** The monthly series reads out the exact figure under the
cursor; a month opens into the full list of its votes. No number on the site exists only
as pixels.
[→ the numbers](https://ogykarzat.hu/ckl43/szamok/index.html)

![The monthly chart reading out an exact value, then a month opening into its votes](docs/media/szamok.gif)

**Search what was actually said.** The verbatim floor record, prefix-searchable in the
browser — no server, the index ships as static shards.
[→ the speech search](https://ogykarzat.hu/ckl43/felszolalas/kereses.html)

![Typing into the speech search; results narrow as the word grows](docs/media/kereses.gif)

## The record it stands on

| | |
|---|---|
| Recorded votes, 1990 → today | **79,829** |
| Roll calls naming every member | **49,771** |
| Individual voting positions | **17,444,402** |
| Unresolved names among them | **0** |
| People who ever held a mandate | **1,602** |
| Floor speeches indexed | **495,191** |
| Sitting days | **1,858** |
| Offline tests guarding all of it | **428** |

Every number in this table — and every number in this repository's prose — is recomputed
by `scripts/check_readme.py` and the test suite fails when the text and the data disagree.
Nothing numeric in this file is typed by hand.

## How it holds

- **One source.** The Országgyűlés's own Web API. No scraping, no third parties, no
  cookies, no analytics. Every figure carries its provenance.
- **Static all the way down.** One Python builder renders the whole site as files.
  What you see needs no server and no JavaScript; scripts only add speed and comfort.
- **Gates before publish.** The full test suite (link resolution across every internal
  reference included), this README's number gate, and an axe-core accessibility audit run
  between build and deploy. A failure leaves the old site live: a stale site is a smaller
  harm than a wrong one.
- **The site counts, it does not judge.** Seven distinct recorded positions are never
  collapsed. A tie is no plurality. What the record does not say, the page does not say.

## Run it

```bash
python3 -m scripts.build_site      # data/derived + config → the whole site under site/
python3 -m unittest discover -s tests -t .
python3 -m scripts.check_readme    # the number gate over this file and the log
```

The derived inputs are committed, so a clone builds the full site offline. Live syncing
needs your own (free) parlament.hu API token — see `.env.example`.

## Data for researchers

Every table the site draws is downloadable: per-cycle CSV/JSON under `adatok/` on the
site, and the whole corpus as eight Parquet files — plus an in-browser SQL console
(DuckDB-WASM) at [ogykarzat.hu/riport](https://ogykarzat.hu/riport/index.html), where the
queries run on your machine and nothing is sent anywhere.

| Layer | Licence |
|---|---|
| Code (this repository) | MIT |
| The parliamentary record | The House's own; no rights conveyed — register your own token |
| Derived data (`data/derived/`) | CC BY 4.0 |
| Member portraits | Served as reduced greyscale copies; status documented on the method page |

## The whole story

The build log — every decision, every correction, every bug with its name on it, in the
order they happened — is a separate, long read:
**[docs/ENGINEERING.md](docs/ENGINEERING.md)**.
