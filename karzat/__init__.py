"""karzat — the Hungarian Országgyűlés, vote by vote, seat by seat.

Package layout:
  api.py      thin client for the parlament.hu Web API (XML over HTTPS, personal token)
  xmlutil.py  XML -> plain dict/list conversion and Hungarian date/time helpers
  cli.py      `python -m karzat ...` : probe, dry-run, sync-votes, sync-mps
"""

__version__ = "0.0.1"
