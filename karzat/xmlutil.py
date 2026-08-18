"""XML and date helpers for the parlament.hu Web API.

The API returns XML only. Older parlament.hu CGIs served ISO-8859-2, so parsing goes
through ElementTree from *bytes* (which honours the declared encoding) rather than
through a decoded string.

Date formats used by the API (from the manual, v2.5):
  p_datum_tol / p_datum_ig : ÉÉÉÉ.HH.NN            e.g. 2026.09.15
  p_szavdatum              : ÉÉÉÉ.HH.NN.ÓÓ:PP:MM   e.g. 2026.09.15.09:37:07
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

DATE_FMT = "%Y.%m.%d"
TS_FMT = "%Y.%m.%d.%H:%M:%S"

_TS_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\.(\d{2}):(\d{2}):(\d{2})$")


def fmt_date(d: date) -> str:
    """date -> 'ÉÉÉÉ.HH.NN'."""
    return d.strftime(DATE_FMT)


def parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), DATE_FMT).date()


def fmt_ts(dt: datetime) -> str:
    """datetime -> 'ÉÉÉÉ.HH.NN.ÓÓ:PP:MM' (the p_szavdatum / idopont form)."""
    return dt.strftime(TS_FMT)


def parse_ts(s: str) -> datetime:
    """'2026.09.15.09:37:07' -> datetime. Raises ValueError on anything else."""
    m = _TS_RE.match(s.strip())
    if not m:
        raise ValueError(f"not an API timestamp: {s!r}")
    return datetime(*(int(g) for g in m.groups()))


def ts_to_slug(s: str) -> str:
    """API timestamp -> filesystem/URL-safe id: '2026.09.15.09:37:07' -> '2026-09-15T09-37-07'."""
    dt = parse_ts(s)
    return dt.strftime("%Y-%m-%dT%H-%M-%S")


def slug_to_ts(slug: str) -> str:
    """Inverse of ts_to_slug."""
    return datetime.strptime(slug, "%Y-%m-%dT%H-%M-%S").strftime(TS_FMT)


def parse_xml(raw: bytes) -> ET.Element:
    """Parse API bytes. Honours the XML declaration's encoding (UTF-8 or ISO-8859-2)."""
    return ET.fromstring(raw)


def declared_encoding(raw: bytes) -> str | None:
    """Return the encoding named in the XML declaration, if any (for the probe command)."""
    head = raw[:200]
    m = re.search(rb'encoding=["\']([A-Za-z0-9._-]+)["\']', head)
    return m.group(1).decode("ascii") if m else None


def to_dict(elem: ET.Element) -> Any:
    """Convert an element tree into plain Python data.

    Rules (deliberately simple; verify against real payloads and adjust):
      * attributes -> keys prefixed with '@'
      * text (stripped) -> '#text' when the element also has attributes/children,
        otherwise the element collapses to its text string
      * repeated child tags -> lists; single child -> nested dict
    """
    d: dict[str, Any] = {f"@{k}": v for k, v in elem.attrib.items()}
    children = list(elem)
    text = (elem.text or "").strip()
    if not children and not d:
        return text
    if text:
        d["#text"] = text
    seen: dict[str, list[Any]] = {}
    for child in children:
        seen.setdefault(child.tag, []).append(to_dict(child))
    for tag, vals in seen.items():
        d[tag] = vals[0] if len(vals) == 1 else vals
    return d


def as_list(v: Any) -> list[Any]:
    """to_dict() collapses single children to a scalar/dict; normalise back to a list."""
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]
