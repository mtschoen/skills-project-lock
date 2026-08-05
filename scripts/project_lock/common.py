"""Primitives shared by the protocol core and the modules layered on it.

These live outside `core` so that peripheral modules (the audit trail, for
one) can reuse them without importing the core and creating a cycle.
"""

from __future__ import annotations

import os
import platform
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def state_directory() -> Path:
    override = os.environ.get("PROJECT_LOCK_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Windows":
        local_application_data = os.environ.get("LOCALAPPDATA")
        if local_application_data:
            return Path(local_application_data) / "project-lock"
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
    return base / "project-lock"
