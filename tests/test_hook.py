from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from project_lock import core

HOOK_PATH = Path(__file__).parents[1] / "hooks" / "pre_tool_use.py"


def run_hook(payload: dict, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def edit_payload(target: Path, session_id: str = "session-a") -> dict:
    return {
        "session_id": session_id,
        "cwd": str(target.parent),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
    }


def test_foreign_lock_denies_edit(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(edit_payload(main / "README.md", session_id="session-a"))
    assert result.returncode == 2
    assert "busy" in result.stderr


def test_relative_target_resolves_against_payload_cwd(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    payload = {
        "session_id": "session-a",
        "cwd": str(main),
        "tool_name": "Edit",
        "tool_input": {"file_path": "README.md"},
    }
    result = run_hook(payload)
    assert result.returncode == 2
    assert "busy" in result.stderr


def test_own_session_lock_allows_edit(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="mine", duration=timedelta(minutes=5), session="session-a")
    result = run_hook(edit_payload(main / "README.md", session_id="session-a"))
    assert result.returncode == 0


def test_sessionless_legacy_lock_denies(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="manual", duration=timedelta(minutes=5))
    result = run_hook(edit_payload(main / "README.md"))
    assert result.returncode == 2


def test_no_lock_denies_with_acquire_recipe(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    result = run_hook(edit_payload(main / "README.md", session_id="session-a"))
    assert result.returncode == 2
    assert "acquire" in result.stderr
    assert "--session session-a" in result.stderr


def test_nested_worktree_not_governed_by_parent_lock(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    core.acquire(nested, reason="mine", duration=timedelta(minutes=5), session="session-a")
    result = run_hook(edit_payload(nested / "inner.py", session_id="session-a"))
    assert result.returncode == 0


def test_warn_mode_allows_with_message(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(edit_payload(main / "README.md"), {"PROJECT_LOCK_ENFORCE": "warn"})
    assert result.returncode == 0
    assert "busy" in result.stderr


def test_off_mode_allows_silently(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(edit_payload(main / "README.md"), {"PROJECT_LOCK_ENFORCE": "off"})
    assert result.returncode == 0
    assert result.stderr == ""


def test_malformed_input_fails_open():
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0


def test_unknown_tool_allows():
    result = run_hook({"session_id": "s", "cwd": ".", "tool_name": "Glob", "tool_input": {}})
    assert result.returncode == 0


def test_governing_lock_in_process_latency(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5))
    target = main / "src" / "deep" / "file.py"
    start = time.perf_counter()
    for _ in range(20):
        core.governing_lock(target)
    elapsed = (time.perf_counter() - start) / 20
    assert elapsed < 0.05


def test_hook_process_end_to_end_budget(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    start = time.perf_counter()
    run_hook(edit_payload(main / "README.md"))
    assert time.perf_counter() - start < 3.0
