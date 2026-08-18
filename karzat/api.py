"""Thin client for the parlament.hu Web API (Országgyűlés "W-API", manual v2.5).

Base URL   https://www.parlament.hu/cgi-bin/web-api-pub/<service>.cgi
Auth       access_token=<personal code>  (GET parameter; obtained by registration; every
           request is logged by OGYH — keep the token server-side and never commit it)
Format     XML

Services and parameters are transcribed from reference/parlament-webapi/*.txt. Two things
the manual does *not* say and this client therefore does not assume: rate limits (none
stated — we pace ourselves anyway) and response schemas (we cache raw bytes and parse
generically until real payloads have been inspected).

Every fetch is cached on disk under data/raw/<service>/<key>.xml so a re-run never hits the
API twice for the same request. Delete a file to force a refetch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .xmlutil import fmt_date, parse_xml, to_dict, ts_to_slug

BASE_URL = "https://www.parlament.hu/cgi-bin/web-api-pub/"
TOKEN_ENV = "PARLAMENT_API_TOKEN"
DEFAULT_CACHE = Path("data/raw")

# service -> (required params, optional params) — straight from the manual
SERVICES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "kepviselok": ((), ("p_sorrend",)),          # active MPs of the current ciklus; p_sorrend=VK alt. order
    "kepviselo": (("p_azon",), ()),                # one MP (ülőhely, frakció history, elections, ...)
    "szavazasok": (("p_datum_tol", "p_datum_ig"), ()),  # votes in [from, to], dates ÉÉÉÉ.HH.NN
    "szavazas": (("p_szavdatum",), ()),            # one vote by idopont ÉÉÉÉ.HH.NN.ÓÓ:PP:MM
    "iromanyok": ((), ("p_all",)),                 # bills of current ciklus; p_all=F -> in-progress only
    "iromany": (("p_izon",), ()),                  # one bill: tulajdonsagok, esemenyek, szavazasok, ...
    "bizottsagok": ((), ("p_aktual",)),            # manual's URL cell is broken; name assumed — VERIFY
    "bizottsag": (("p_biz",), ()),
    "szoszolok": ((), ()),                         # nationality spokespersons
    "ulesnap": (("p_ckl",), ()),                   # sitting days of a ciklus (data from ciklus 36)
    "felszolalasok": (("p_ckl", "p_nap"), ()),     # speeches of one sitting day
    "felszolalas": (("p_ckl", "p_uln", "p_felsz"), ()),  # one speech (text)
}


class ApiError(RuntimeError):
    pass


class MissingToken(ApiError):
    pass


def build_url(service: str, params: dict[str, Any], token: str | None) -> str:
    """Assemble a request URL; validates the service name and required parameters."""
    if service not in SERVICES:
        raise ApiError(f"unknown service {service!r}; known: {', '.join(SERVICES)}")
    required, optional = SERVICES[service]
    missing = [p for p in required if p not in params or params[p] in (None, "")]
    if missing:
        raise ApiError(f"{service}: missing required parameter(s) {missing}")
    unknown = [p for p in params if p not in required + optional]
    if unknown:
        raise ApiError(f"{service}: unknown parameter(s) {unknown}")
    q: dict[str, Any] = {}
    if token:
        q["access_token"] = token
    q.update({k: v for k, v in params.items() if v not in (None, "")})
    return f"{BASE_URL}{service}.cgi?{urlencode(q)}"


def mask_token(url: str) -> str:
    return re.sub(r"(access_token=)[^&]+", r"\1***", url)


def cache_key(service: str, params: dict[str, Any]) -> str:
    """Readable cache file names for the hot paths, a hash for everything else."""
    if service == "szavazas" and "p_szavdatum" in params:
        return ts_to_slug(str(params["p_szavdatum"]))
    if service == "szavazasok":
        return f"{params['p_datum_tol']}__{params['p_datum_ig']}".replace(".", "-")
    if service == "kepviselo":
        return f"mp_{params['p_azon']}"
    if service == "iromany":
        return f"iromany_{params['p_izon']}"
    if not params:
        return "all"
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


@dataclass
class WebApi:
    token: str | None = None
    cache_dir: Path = DEFAULT_CACHE
    min_interval: float = 0.6          # seconds between live requests — be a polite guest
    timeout: float = 60.0
    user_agent: str = "karzat/0.0.1 (+https://github.com/abognar-git)"
    session: requests.Session = field(default_factory=requests.Session)
    _last_call: float = field(default=0.0, init=False, repr=False)
    live_calls: int = field(default=0, init=False)
    cache_hits: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.token is None:
            self.token = os.environ.get(TOKEN_ENV) or None
        self.cache_dir = Path(self.cache_dir)
        self.session.headers.setdefault("User-Agent", self.user_agent)

    # -- low level ---------------------------------------------------------------------
    def cache_path(self, service: str, params: dict[str, Any]) -> Path:
        return self.cache_dir / service / f"{cache_key(service, params)}.xml"

    retries: int = 2                   # extra attempts on a 5xx / timeout / connection error, with backoff
    max_consecutive_failures: int = 5  # after this many failed live calls in a row, stop asking (circuit breaker)
    consecutive_failures: int = field(default=0, init=False)

    def fetch(self, service: str, *, refresh: bool = False, **params: Any) -> bytes:
        """Return raw XML bytes for a service call, from cache when possible."""
        path = self.cache_path(service, params)
        if path.exists() and not refresh:
            self.cache_hits += 1
            return path.read_bytes()
        if not self.token:
            raise MissingToken(
                f"no access token: set {TOKEN_ENV} in the environment (see .env.example)"
            )
        if self.consecutive_failures >= self.max_consecutive_failures:
            raise ApiError(f"{service}: {self.consecutive_failures} live calls failed in a row — stopping rather than hammering the API")
        url = build_url(service, params, self.token)
        masked = mask_token(url)
        attempt = 0
        while True:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_call = time.monotonic()
                self.live_calls += 1
                status = resp.status_code
                err = None if status == 200 else f"HTTP {status}"
            except requests.RequestException as e:
                # requests embeds the full URL (token included) in its messages: never let that text out
                self._last_call = time.monotonic()
                self.live_calls += 1
                resp, status, err = None, None, type(e).__name__
            if err is None:
                break
            transient = status is None or status >= 500 or status == 429
            if transient and attempt < self.retries:
                attempt += 1
                time.sleep(min(30.0, self.min_interval * (2 ** attempt)))
                continue
            self.consecutive_failures += 1
            raise ApiError(f"{service}: {err} for {masked}")
        raw = resp.content
        head = raw.lstrip()[:512].lower()
        if not head.startswith(b"<") or b"<html" in head or b"<!doctype" in head:
            # The CGI answers HTML (or a bare error string) for bad tokens/params; do not cache it.
            self.consecutive_failures += 1
            snippet = raw[:160].decode("utf-8", "replace").replace(self.token, "***")
            raise ApiError(f"{service}: non-XML response ({len(raw)} bytes) for {masked}: {snippet!r}")
        self.consecutive_failures = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def get(self, service: str, **params: Any) -> Any:
        """fetch() + parse to plain dict/list (see xmlutil.to_dict)."""
        return to_dict(parse_xml(self.fetch(service, **params)))

    # -- typed convenience wrappers ------------------------------------------------------
    def kepviselok(self, sorrend: str | None = None) -> Any:
        return self.get("kepviselok", p_sorrend=sorrend)

    def kepviselo(self, azon: str) -> Any:
        return self.get("kepviselo", p_azon=azon)

    def szavazasok(self, date_from: date, date_to: date) -> Any:
        return self.get("szavazasok", p_datum_tol=fmt_date(date_from), p_datum_ig=fmt_date(date_to))

    def szavazas(self, idopont: str) -> Any:
        """idopont in the API's own form, e.g. '2026.09.15.09:37:07'."""
        return self.get("szavazas", p_szavdatum=idopont)

    def iromanyok(self, in_progress_only: bool = False) -> Any:
        return self.get("iromanyok", p_all="F" if in_progress_only else None)

    def iromany(self, izon: str) -> Any:
        return self.get("iromany", p_izon=izon)

    def bizottsagok(self, current_only: bool = False) -> Any:
        return self.get("bizottsagok", p_aktual="i" if current_only else None)

    def bizottsag(self, biz: str) -> Any:
        return self.get("bizottsag", p_biz=biz)

    def szoszolok(self) -> Any:
        return self.get("szoszolok")

    def ulesnap(self, ckl: int) -> Any:
        return self.get("ulesnap", p_ckl=ckl)

    def felszolalasok(self, ckl: int, nap: int) -> Any:
        return self.get("felszolalasok", p_ckl=ckl, p_nap=nap)

    def felszolalas(self, ckl: int, uln: int, felsz: int) -> Any:
        return self.get("felszolalas", p_ckl=ckl, p_uln=uln, p_felsz=felsz)
