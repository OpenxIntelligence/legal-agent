"""Connector contracts for future external legal sources.

Defines what a connector must declare before any artifact may
use it. No connector is configured in this milestone: local
upload and synthetic fixtures are the only sources. Later
increments add integrations against this contract instead of
inventing a second permission model.

A configured connector is a dict with every REQUIRED field:
- id: stable connector key (e.g. "room-a").
- system: the external system and record version it reads.
- auth: per-user scope description; viewers never inherit
  the owner's credentials.
- can_write: false for every read-only integration.
- freshness: how current the data is and when it was last
  checked; unknown stays unknown.
- revocation: how deleted or revoked sources surface.
- limits: rate, cost, and API bounds with retry policy.
- record_uri: reproducible reference for cited records.
- retention: minimization, encryption, retention, and audit
  handling.

Malformed entries fail closed: they raise instead of
yielding a half-permissioned connector.
"""

from __future__ import annotations

from typing import Any

from .case_record import CaseError

REQUIRED_FIELDS = (
    "id",
    "system",
    "auth",
    "can_write",
    "freshness",
    "revocation",
    "limits",
    "record_uri",
    "retention",
)


def configured(data: dict) -> list[dict]:
    """Validated connectors on this record (empty today).

    Unknown shapes raise: a connector that cannot be fully
    described cannot be used.
    """
    entries = data.get("connectors", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise CaseError("connectors must be a list")
    valid = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CaseError("a connector entry must be an object")
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            raise CaseError(
                f"connector {entry.get('id', '?')!r} is missing: "
                + ", ".join(missing)
            )
        if not isinstance(entry["can_write"], bool):
            raise CaseError(
                f"connector {entry.get('id', '?')!r} must declare "
                "can_write as true or false"
            )
        valid.append({field: entry[field] for field in REQUIRED_FIELDS})
    return valid
