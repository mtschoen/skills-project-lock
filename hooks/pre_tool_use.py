#!/usr/bin/env python3
"""Claude Code PreToolUse hook: mechanical enforcement of project locks.

Denies Edit/Write/NotebookEdit calls whose target is governed by another
session's cooperative lock, and denies unlocked writes with the exact acquire
command. Bash coverage is bounded: cwd jurisdiction plus absolute-path-token
scan. Fail-open by design: cooperative coordination must not brick the
harness on its own bugs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from project_lock.core import governing_lock, nearest_worktree_root

FILE_TOOL_PATH_KEYS = {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
ALLOW = 0
DENY = 2


def enforcement_mode() -> str:
    return os.environ.get("PROJECT_LOCK_ENFORCE", "deny").strip().lower()


def lock_is_foreign(lock: dict | None, session_id: str) -> bool:
    if lock is None:
        return True
    session = lock.get("session")
    return not session or session != session_id


def describe_lock(governed: dict) -> str:
    lock = governed["lock"] or {}
    owner = lock.get("owner", "unknown")
    reason = lock.get("reason", "metadata unavailable")
    expected = lock.get("expected_until", "unknown")
    return (
        f"project-lock: {governed['root']} is locked by {owner}\n"
        f"  reason: {reason}\n  expected until: {expected}\n"
        "  Wait, work elsewhere, or use a separate git worktree. Overdue "
        "locks are not free; contact the owner before any force-clear."
    )


def acquire_recipe(target: str, session_id: str) -> str:
    root = nearest_worktree_root(target)
    script = Path(__file__).resolve().parents[1] / "scripts" / "project-lock.py"
    session_flag = f" --session {session_id}" if session_id else ""
    return (
        f"project-lock: no lock held for {root}\n"
        "  Acquire one before writing:\n"
        f'  python "{script}" acquire "{root}" --reason "<why>" '
        f"--duration 30m{session_flag}\n"
        "  Then retry this edit."
    )


def resolve_target(target: str, cwd: str | None) -> str:
    path = Path(target)
    if path.is_absolute():
        return target
    return str(Path(cwd or ".") / path)


def check_file_tool(payload: dict) -> tuple[int, str]:
    target = payload.get("tool_input", {}).get(FILE_TOOL_PATH_KEYS[payload["tool_name"]])
    if not target:
        return ALLOW, ""
    target = resolve_target(target, payload.get("cwd"))
    session_id = payload.get("session_id", "")
    governed = governing_lock(target)
    if governed is None:
        return DENY, acquire_recipe(target, session_id)
    if lock_is_foreign(governed["lock"], session_id):
        return DENY, describe_lock(governed)
    return ALLOW, ""


def evaluate(payload: dict) -> tuple[int, str]:
    tool_name = payload.get("tool_name", "")
    if tool_name in FILE_TOOL_PATH_KEYS:
        return check_file_tool(payload)
    return ALLOW, ""


def main() -> int:
    mode = enforcement_mode()
    if mode == "off":
        return ALLOW
    try:
        payload = json.load(sys.stdin)
        decision, message = evaluate(payload)
    except Exception as error:  # fail-open by design
        print(f"project-lock hook error (allowing): {error}", file=sys.stderr)
        return ALLOW
    if message:
        print(message, file=sys.stderr)
    if mode == "warn":
        return ALLOW
    return decision


if __name__ == "__main__":
    sys.exit(main())
