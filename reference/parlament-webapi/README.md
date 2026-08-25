# The Web API manual (v2.5) is not in this repository

The Országgyűlés Hivatala hands its W-API user manual to each registrant personally, and states
no redistribution licence for it. So this directory points at the document instead of copying it:
the repository's claims about the API are checkable, but the House's file stays the House's.

**How to get it:** ask for API access at `api-reg2@parlament.hu`. The registration reply carries
both the personal access token and the manual (`webapi_felhasznaloi_kezikonyv_v2.5.pdf`).
Drop the file in this directory — `.gitignore` already keeps it out of commits.

**What was transcribed from it** — and is therefore in the repository, as code rather than as prose:

| Where | What |
|---|---|
| `karzat/api.py` → `SERVICES` | the twelve service endpoints and every parameter each one takes |
| `karzat/api.py` docstring | that the format is XML, the token is personal, and every request is logged |
| `karzat/majority.py` | nothing — the majority rules come from the Alaptörvény and the HHSZ, cited there against njt.hu |

Two things the manual does **not** state, which this client therefore does not assume: rate limits
(none are given — the client paces itself anyway) and response schemas (raw bytes are cached and
parsed generically, against real payloads).
