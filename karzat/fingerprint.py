"""Deterministic fingerprints of payloads and records, for tests/test_golden.py.

A fingerprint is the first 16 hex digits of SHA-256 over the *canonical* JSON of an object:
keys sorted, no whitespace, non-ASCII preserved. The same XML parsed by the same converter
always yields the same digest; any change to xmlutil.to_dict() — or, later, to the
normaliser — changes the digest and the golden test says so.

    python3 -m karzat fingerprint tests/fixtures/*.xml      # table to paste into GOLDEN

Fingerprints are of *our reading* of a payload, not of the bytes: whitespace or attribute
order changes in the source do not move them, a parsing change does. That is the property
a golden test wants.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .xmlutil import parse_xml, to_dict


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()[:16]


def fingerprint_xml_bytes(raw: bytes) -> str:
    """Digest of to_dict(parse_xml(raw)) — the structure this codebase actually consumes."""
    root = parse_xml(raw)
    return digest({root.tag: to_dict(root)})


def fingerprint_file(path: Path) -> str:
    return fingerprint_xml_bytes(Path(path).read_bytes())


def table(paths: list[Path]) -> str:
    """Lines ready to paste into tests/test_golden.py's GOLDEN dict."""
    width = max((len(p.name) for p in paths), default=0) + 3
    return "\n".join(f'    "{p.name}":{" " * (width - len(p.name))}"{fingerprint_file(p)}",' for p in paths)
