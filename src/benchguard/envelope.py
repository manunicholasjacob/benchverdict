"""The shared measurement-provenance envelope.

``measurement-provenance/1`` is a small outer JSON contract shared by a family
of measurement tools so that a consumer can read any of their outputs with one
parser. benchguard owns the ``verdict`` and ``reasons`` concepts in that
contract; every tool-specific field lives under ``measurement``.

The authoritative schema ships in this package as
``benchguard/schema/measurement-provenance-1.schema.json`` and is reachable at
runtime through :func:`schema_path` and :func:`load_schema`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import sys
from typing import Any, Dict, Iterable, List, Optional

from ._version import SCHEMA_ID, __version__
from .checks import Reason

__all__ = [
    "SCHEMA_ID",
    "build_envelope",
    "platform_block",
    "schema_path",
    "load_schema",
    "validate_envelope",
]

SCHEMA_FILENAME = "measurement-provenance-1.schema.json"


def schema_path() -> str:
    """Absolute path to the bundled JSON Schema for the envelope."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema", SCHEMA_FILENAME)


def load_schema() -> Dict[str, Any]:
    """The bundled JSON Schema, parsed."""
    with open(schema_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


def platform_block() -> Dict[str, str]:
    """The ``platform`` block of the envelope, from the standard library only."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": "%d.%d.%d" % sys.version_info[:3],
    }


def _utc_now() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_envelope(
    verdict: str,
    reasons: Iterable[Reason],
    measurement: Dict[str, Any],
    duration_s: float,
    unreadable: Optional[Iterable[str]] = None,
    taken_at_utc: Optional[str] = None,
    tool_name: str = "benchguard",
    tool_version: str = __version__,
) -> Dict[str, Any]:
    """Assemble a ``measurement-provenance/1`` envelope.

    The key order below is the documented order of the contract. Sibling tools
    emit the same outer keys and differ only under ``measurement``.
    """
    return {
        "schema": SCHEMA_ID,
        "tool": {"name": tool_name, "version": tool_version},
        "taken_at_utc": taken_at_utc or _utc_now(),
        "duration_s": float(duration_s),
        "platform": platform_block(),
        "verdict": verdict,
        "reasons": [r.to_dict() for r in reasons],
        "unreadable": list(unreadable or []),
        "measurement": measurement,
    }


_REQUIRED_TOP_LEVEL = (
    "schema",
    "tool",
    "taken_at_utc",
    "duration_s",
    "platform",
    "verdict",
    "reasons",
    "unreadable",
    "measurement",
)
_VALID_VERDICTS = ("PASS", "INVALID", "UNSUPPORTED", "UNKNOWN")
_VALID_SEVERITIES = ("invalidating", "warning", "info")


def validate_envelope(envelope: Dict[str, Any]) -> List[str]:
    """Check an envelope against the contract without needing ``jsonschema``.

    Returns a list of human-readable problems. An empty list means the envelope
    satisfies every constraint this function knows how to check. This is a
    dependency-free convenience, not a replacement for a real JSON Schema
    validator run against :func:`schema_path`.
    """
    problems: List[str] = []
    for key in _REQUIRED_TOP_LEVEL:
        if key not in envelope:
            problems.append("missing required top-level key: %s" % key)
    if envelope.get("schema") != SCHEMA_ID:
        problems.append("schema must be %r, got %r" % (SCHEMA_ID, envelope.get("schema")))
    tool = envelope.get("tool")
    if not isinstance(tool, dict) or "name" not in tool or "version" not in tool:
        problems.append("tool must be an object with name and version")
    taken = envelope.get("taken_at_utc")
    if not isinstance(taken, str) or not taken.endswith("Z"):
        problems.append("taken_at_utc must be an ISO 8601 UTC string ending in Z")
    if not isinstance(envelope.get("duration_s"), (int, float)) or isinstance(
        envelope.get("duration_s"), bool
    ):
        problems.append("duration_s must be a number")
    plat = envelope.get("platform")
    if not isinstance(plat, dict):
        problems.append("platform must be an object")
    else:
        for key in ("system", "release", "machine", "python"):
            if key not in plat:
                problems.append("platform missing key: %s" % key)
    if envelope.get("verdict") not in _VALID_VERDICTS:
        problems.append(
            "verdict must be one of %s, got %r" % (list(_VALID_VERDICTS), envelope.get("verdict"))
        )
    reasons = envelope.get("reasons")
    if not isinstance(reasons, list):
        problems.append("reasons must be an array")
    else:
        for i, r in enumerate(reasons):
            if not isinstance(r, dict):
                problems.append("reasons[%d] must be an object" % i)
                continue
            for key in ("code", "severity", "detail"):
                if key not in r:
                    problems.append("reasons[%d] missing key: %s" % (i, key))
            if r.get("severity") not in _VALID_SEVERITIES:
                problems.append(
                    "reasons[%d].severity must be one of %s, got %r"
                    % (i, list(_VALID_SEVERITIES), r.get("severity"))
                )
            code = r.get("code")
            if isinstance(code, str) and not code.replace("_", "").isalnum():
                problems.append("reasons[%d].code must be SCREAMING_SNAKE: %r" % (i, code))
            if isinstance(code, str) and code != code.upper():
                problems.append("reasons[%d].code must be upper case: %r" % (i, code))
    if not isinstance(envelope.get("unreadable"), list):
        problems.append("unreadable must be an array of strings")
    if not isinstance(envelope.get("measurement"), dict):
        problems.append("measurement must be an object")
    return problems
