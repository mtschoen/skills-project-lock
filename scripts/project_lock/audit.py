"""Append-only audit trail for lock overrides.

Only force-clears are recorded. An owner releasing its own lock proves
ownership with a lock id and needs no trail; a force-clear discards somebody
else's claim, so it must leave behind which lock died, who held it, and why.

The record is written *before* the lock is removed, which makes "no unaudited
force-clear" an invariant rather than a best effort: a trail that cannot be
written fails the override and leaves the lock standing. The cost is that a
clear failing later still leaves its record behind, which is the harmless
direction to be wrong in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import format_time, state_directory, utc_now

AUDIT_FILE_NAME = "audit.jsonl"


def audit_path() -> Path:
    return state_directory() / AUDIT_FILE_NAME


def record(action: str, **fields: Any) -> None:
    """Append one audit record, raising if the trail cannot be written."""
    path = audit_path()
    payload = {"at": format_time(utc_now()), "action": action, **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, sort_keys=True) + "\n")


def record_force_release(root: str, metadata: dict[str, Any] | None, reason: str) -> None:
    record(
        "force-release",
        root=root,
        cleared_lock_id=None if metadata is None else metadata["lock_id"],
        owner=None if metadata is None else metadata.get("owner"),
        session=None if metadata is None else metadata.get("session"),
        reason=reason,
    )
