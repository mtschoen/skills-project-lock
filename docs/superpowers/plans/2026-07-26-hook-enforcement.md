# Hook Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mechanically prevent cross-agent write races by adding a Claude Code
`PreToolUse` hook that denies file-tool writes governed by another session's
lock (deny-with-recipe when no lock is held), with bounded Bash coverage, plus
nested-worktree jurisdiction semantics that resolve the worktree-as-subfolder
case.

**Architecture:** A new git-free jurisdiction resolver in `core.py`
(`governing_lock` walks ancestors for `.agent-lock/` markers and stops at
`.git` boundaries; `related_locks` reports ancestor/descendant locks for
visibility). A standalone stdlib hook script `hooks/pre_tool_use.py` reads
Claude-shaped JSON on stdin and exits 2 to deny. The existing cooperative CLI,
mkdir-marker protocol, and prose guidance stay; `owner.json` already carries an
optional `session` field, which the hook matches against the harness session
id. No daemon, no OS-level machinery, no shell parsing beyond a frozen
read-only allowlist and an absolute-path-token scan.

**Tech Stack:** Python standard library only. pytest with real `git init` /
`git worktree add` fixtures. Gates: `ruff check scripts tests`,
`ruff format --check scripts tests`, `uvx --from skills-ref==0.1.1 agentskills
validate ../project-lock`, `aislop ci .`.

## Global Constraints

- Runtime dependencies: Python standard library only (repo rule).
- Lock acquisition must remain one atomic `mkdir` (repo rule) - this plan never
  changes acquisition.
- `owner.json` backward readability is a public protocol (repo rule): additive
  changes only; `PROTOCOL_VERSION` stays 1; absence of `session` means a
  legacy/manual lock and is treated as foreign by enforcement.
- No machine-specific paths in shipped files (repo rule).
- Enforcement modes via env `PROJECT_LOCK_ENFORCE`: `deny` (default), `warn`
  (report, allow), `off`.
- Hook failure policy: any unhandled error -> allow (exit 0) with a one-line
  stderr diagnostic. Denial is ONLY exit code 2.
- Hook hot path performs zero subprocess calls (no git). In-process
  `governing_lock` budget: under 50ms; end-to-end hook process budget in tests:
  under 3s (CI-safe bound covering interpreter startup).
- An overdue foreign lock still denies (overdue means the estimate was wrong,
  not that the lock is free).
- Existing tests must stay green; keep `TEST-REPORT.md` current and follow
  `SMOKE.md` before release.
- Commit style: `<type>: <imperative summary>`, body explains why, no
  em-dashes anywhere.

---

## Phase 1: Jurisdiction core

### Task 1: Worktree fixtures

**Files:**
- Modify: `tests/conftest.py`
- Test: (fixtures only; exercised by later tasks)

**Interfaces:**
- Produces: pytest fixtures `nested_worktree_repo` returning a dict with keys
  `main` (Path of main checkout), `nested` (Path of linked worktree at
  `<main>/.worktrees/feature`), `sibling` (Path of linked worktree at
  `<tmp>/sibling`). All later tasks consume these names.

- [ ] **Step 1: Add git helpers and fixtures to conftest**

Append to `tests/conftest.py`:

```python
import subprocess


def run_git_command(*arguments: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
    )


@pytest.fixture
def nested_worktree_repo(tmp_path: Path) -> dict[str, Path]:
    main = tmp_path / "main"
    main.mkdir()
    run_git_command("init", "-q", cwd=main)
    (main / "README.md").write_text("seed\n", encoding="utf-8")
    run_git_command("add", "README.md", cwd=main)
    run_git_command("commit", "-q", "-m", "seed", cwd=main)
    nested = main / ".worktrees" / "feature"
    run_git_command("worktree", "add", "-q", str(nested), "-b", "feature", cwd=main)
    sibling = tmp_path / "sibling"
    run_git_command("worktree", "add", "-q", str(sibling), "-b", "sibling", cwd=main)
    return {"main": main, "nested": nested, "sibling": sibling}
```

Also add `import os` to the conftest imports.

- [ ] **Step 2: Sanity-run the suite**

Run: `python -m pytest -q` (from the project-lock repo root)
Expected: existing tests PASS, no collection errors.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add nested and sibling worktree fixtures"
```

### Task 2: governing_lock resolver

**Files:**
- Modify: `scripts/project_lock/core.py`
- Modify: `scripts/project_lock/__init__.py` (re-export)
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `governing_lock(path: Path | str) -> dict[str, Any] | None` -
  returns `{"root": str, "lock": dict | None}` for the nearest governing
  marker (`lock` is None for a damaged/initializing marker), or None when no
  marker governs the path. Walks up from the deepest EXISTING ancestor of
  `path`; inspects `.agent-lock` before honoring a `.git` boundary stop.
- Produces: `nearest_worktree_root(path: Path | str) -> Path` - nearest
  ancestor containing `.git` (dir or file), else the deepest existing
  directory of `path`. Used for deny-recipe text. Both are subprocess-free.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_core.py`:

```python
def test_governing_lock_finds_ancestor_within_worktree(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="r", duration=timedelta(minutes=5))
    governed = core.governing_lock(main / "src" / "new_file.py")
    assert governed is not None
    assert governed["root"] == str(core.canonical_path(main))
    assert governed["lock"]["reason"] == "r"


def test_governing_lock_stops_at_nested_worktree_boundary(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(main, reason="r", duration=timedelta(minutes=5))
    assert core.governing_lock(nested / "inner.py") is None


def test_governing_lock_sees_nested_lock_not_parent(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(main, reason="parent", duration=timedelta(minutes=5))
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    governed = core.governing_lock(nested / "inner.py")
    assert governed["lock"]["reason"] == "child"


def test_governing_lock_damaged_marker_reports_none_lock(tmp_path):
    project = tmp_path / "plain"
    project.mkdir()
    (project / ".agent-lock").mkdir()
    governed = core.governing_lock(project / "file.txt")
    assert governed == {"root": str(core.canonical_path(project)), "lock": None}


def test_governing_lock_no_marker_returns_none(nested_worktree_repo):
    assert core.governing_lock(nested_worktree_repo["sibling"] / "x.py") is None


def test_nearest_worktree_root(nested_worktree_repo, tmp_path):
    nested = nested_worktree_repo["nested"]
    assert core.nearest_worktree_root(nested / "deep" / "x.py") == core.canonical_path(nested)
    plain = tmp_path / "plain2"
    plain.mkdir()
    assert core.nearest_worktree_root(plain / "x.py") == core.canonical_path(plain)
```

(`test_core.py` already imports `core` and `timedelta`; verify and reuse its
import style.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_core.py -q -k governing_or_nearest --no-header`
(use `-k "governing_lock or nearest_worktree"`)
Expected: FAIL with `AttributeError: ... has no attribute 'governing_lock'`.

- [ ] **Step 3: Implement in core.py**

Add after `resolve_root`:

```python
def deepest_existing_directory(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if candidate.is_file():
        candidate = candidate.parent
    return canonical_path(candidate)


def nearest_worktree_root(path: Path | str) -> Path:
    start = deepest_existing_directory(path)
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return start
        current = parent


def governing_lock(path: Path | str) -> dict[str, Any] | None:
    current = deepest_existing_directory(path)
    while True:
        marker = current / MARKER_DIRECTORY_NAME
        if marker.exists():
            metadata = valid_metadata(read_json(marker / METADATA_FILE_NAME))
            return {"root": str(current), "lock": metadata}
        if (current / ".git").exists():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
```

Re-export all three names in `scripts/project_lock/__init__.py` following its
existing pattern.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_core.py -q`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/project_lock/core.py scripts/project_lock/__init__.py tests/test_core.py
git commit -m "feat: git-free governing-lock jurisdiction resolver"
```

### Task 3: related_locks visibility

**Files:**
- Modify: `scripts/project_lock/core.py`, `scripts/project_lock/__init__.py`
- Modify: `scripts/project_lock/cli.py` (print related lines in `print_status`
  callers via `inspect` payload)
- Test: `tests/test_core.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `related_locks(root: Path | str) -> dict[str, list[dict[str, Any]]]`
  with keys `ancestors` (locks above `root`, deliberately including beyond
  `.git` boundaries) and `descendants` (registry entries whose root is under
  `root` and whose marker still exists). Each entry:
  `{"root": str, "lock": dict}`.
- Modifies: `inspect()` result gains a `"related"` key (the `related_locks`
  payload) whenever either list is nonempty. `command_check` prints one line
  per related lock: `  related: ancestor <root> held by <owner>` /
  `  related: nested <root> held by <owner>`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_core.py`:

```python
def test_related_locks_reports_descendant_and_ancestor(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    related = core.related_locks(main)
    assert [entry["lock"]["reason"] for entry in related["descendants"]] == ["child"]
    related_from_nested = core.related_locks(nested)
    assert related_from_nested["descendants"] == []
    core.acquire(main, reason="parent", duration=timedelta(minutes=5))
    related_from_nested = core.related_locks(nested)
    assert [e["lock"]["reason"] for e in related_from_nested["ancestors"]] == ["parent"]


def test_inspect_includes_related(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    status = core.inspect(main)
    assert status["related"]["descendants"][0]["root"] == str(core.canonical_path(nested))
```

Append to `tests/test_cli.py` (mirroring its existing CLI-driving style):

```python
def test_check_prints_related_nested_lock(nested_worktree_repo, capsys):
    nested = nested_worktree_repo["nested"]
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    exit_code = run_cli("check", str(nested_worktree_repo["main"]))
    output = capsys.readouterr().out
    assert "related: nested" in output
    assert exit_code == 0
```

(Adapt `run_cli` to whatever helper `tests/test_cli.py` already uses for
invoking the CLI; reuse its import of `core`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_core.py tests/test_cli.py -q -k related`
Expected: FAIL with `AttributeError: ... 'related_locks'`.

- [ ] **Step 3: Implement**

In `core.py`, add after `governing_lock`:

```python
def related_locks(root: Path | str) -> dict[str, list[dict[str, Any]]]:
    base = canonical_path(Path(root).expanduser())
    ancestors: list[dict[str, Any]] = []
    current = base.parent
    while current != current.parent:
        metadata = valid_metadata(read_json(current / MARKER_DIRECTORY_NAME / METADATA_FILE_NAME))
        if metadata is not None:
            ancestors.append({"root": str(current), "lock": metadata})
        current = current.parent
    descendants: list[dict[str, Any]] = []
    prefix = str(base).rstrip("\\/") + os.sep
    registry_directory = state_directory() / "locks"
    if registry_directory.exists():
        for entry in sorted(registry_directory.glob("*.json")):
            metadata = valid_metadata(read_json(entry))
            if metadata is None:
                continue
            lock_root = metadata.get("root", "")
            if not lock_root.startswith(prefix):
                continue
            if (Path(lock_root) / MARKER_DIRECTORY_NAME).exists():
                descendants.append({"root": lock_root, "lock": metadata})
    return {"ancestors": ancestors, "descendants": descendants}
```

In `inspect()`, before each `return`, compute
`related = related_locks(root)` and add `"related": related` to the returned
dict when `related["ancestors"] or related["descendants"]` (add to BOTH the
locked and unlocked return payloads).

In `cli.py` `print_status`, after the existing lines, add:

```python
    for entry in status.get("related", {}).get("ancestors", []):
        print(f"  related: ancestor {entry['root']} held by {entry['lock']['owner']}")
    for entry in status.get("related", {}).get("descendants", []):
        print(f"  related: nested {entry['root']} held by {entry['lock']['owner']}")
```

and mirror the same two loops in the `FREE` early-return branch (print them
after the `FREE` line, then `return`).

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/project_lock/core.py scripts/project_lock/__init__.py scripts/project_lock/cli.py tests/test_core.py tests/test_cli.py
git commit -m "feat: report related ancestor and nested locks in check output"
```

## Phase 2: Enforcement hook - file tools

### Task 4: Hook skeleton with file-tool enforcement

**Files:**
- Create: `hooks/pre_tool_use.py`
- Create: `tests/test_hook.py`

**Interfaces:**
- Consumes: `governing_lock`, `nearest_worktree_root` from Task 2.
- Produces: executable `hooks/pre_tool_use.py`. Stdin: Claude Code PreToolUse
  JSON (`{"session_id": str, "cwd": str, "tool_name": str, "tool_input": {...}}`).
  Exit 0 = allow, exit 2 + stderr = deny. Env `PROJECT_LOCK_ENFORCE`
  (`deny`/`warn`/`off`), `PROJECT_LOCK_STATE_DIR` respected via core.
  Test helper `run_hook(payload: dict, env: dict) -> subprocess.CompletedProcess`
  used by Task 5 tests.

- [ ] **Step 1: Write failing tests**

Create `tests/test_hook.py`:

```python
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
    result = run_hook(
        edit_payload(main / "README.md"), {"PROJECT_LOCK_ENFORCE": "warn"}
    )
    assert result.returncode == 0
    assert "busy" in result.stderr


def test_off_mode_allows_silently(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(
        edit_payload(main / "README.md"), {"PROJECT_LOCK_ENFORCE": "off"}
    )
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
```

Note: `conftest.py`'s `isolated_state_directory` sets
`PROJECT_LOCK_STATE_DIR` via monkeypatch env, which `run_hook` inherits
through `os.environ` - verify this holds (monkeypatch.setenv mutates
`os.environ`), so the hook subprocess sees the isolated state dir.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hook.py -q`
Expected: FAIL (hook file does not exist; subprocess returncode 2 from python
"can't open file" makes assertions fail loudly - confirm failures are for the
right reason before proceeding).

- [ ] **Step 3: Implement the hook (file tools + modes + fail-open)**

Create `hooks/pre_tool_use.py`:

```python
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

from project_lock.core import (  # noqa: E402
    MARKER_DIRECTORY_NAME,
    governing_lock,
    nearest_worktree_root,
)

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
    return (
        f"project-lock: no lock held for {root}\n"
        "  Acquire one before writing:\n"
        f'  python "{script}" acquire "{root}" --reason "<why>" '
        f"--duration 30m --session {session_id}\n"
        "  Then retry this edit."
    )


def check_file_tool(payload: dict) -> tuple[int, str]:
    target = payload.get("tool_input", {}).get(FILE_TOOL_PATH_KEYS[payload["tool_name"]])
    if not target:
        return ALLOW, ""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hook.py -q`
Expected: PASS (Bash tests come in Task 5; only the tests above exist now).

- [ ] **Step 5: Commit**

```bash
git add hooks/pre_tool_use.py tests/test_hook.py
git commit -m "feat: PreToolUse hook denies file-tool writes under foreign locks"
```

## Phase 3: Enforcement hook - Bash

### Task 5: Bash cwd-jurisdiction and path-token checks

**Files:**
- Modify: `hooks/pre_tool_use.py`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `run_hook` helper from Task 4.
- Produces: Bash handling inside `evaluate`. Frozen sets
  `READ_ONLY_COMMANDS` and `GIT_READ_SUBCOMMANDS` (exact contents in Step 3);
  `PATH_TOKEN_PATTERN`; `registry_lock_roots() -> list[tuple[str, dict]]`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_hook.py`:

```python
def bash_payload(command: str, cwd: Path, session_id: str = "session-a") -> dict:
    return {
        "session_id": session_id,
        "cwd": str(cwd),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def test_bash_in_foreign_cwd_denies_mutation(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(bash_payload("touch new.txt", main))
    assert result.returncode == 2


def test_bash_in_foreign_cwd_allows_read_only(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    for command in ("ls -la", "git status", "git log --oneline -5", "rg pattern ."):
        result = run_hook(bash_payload(command, main))
        assert result.returncode == 0, command


def test_bash_compound_with_mutating_segment_denies(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(bash_payload("git status && git commit -m x", main))
    assert result.returncode == 2


def test_bash_own_session_cwd_allows(nested_worktree_repo):
    main = nested_worktree_repo["main"]
    core.acquire(main, reason="mine", duration=timedelta(minutes=5), session="session-a")
    result = run_hook(bash_payload("touch new.txt", main))
    assert result.returncode == 0


def test_bash_unlocked_cwd_allows(nested_worktree_repo):
    result = run_hook(bash_payload("touch new.txt", nested_worktree_repo["sibling"]))
    assert result.returncode == 0


def test_bash_absolute_path_token_under_foreign_lock_denies(nested_worktree_repo, tmp_path):
    main = nested_worktree_repo["main"]
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    core.acquire(main, reason="busy", duration=timedelta(minutes=5), session="session-b")
    result = run_hook(bash_payload(f'echo x > "{main / "out.txt"}"', outside))
    assert result.returncode == 2
    assert str(core.canonical_path(main)) in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hook.py -q -k bash`
Expected: the deny cases FAIL (hook currently allows all Bash).

- [ ] **Step 3: Implement Bash handling**

Add to `hooks/pre_tool_use.py` (imports: `re`; from core also
`state_directory`, `read_json`, `valid_metadata`):

```python
READ_ONLY_COMMANDS = frozenset(
    {"ls", "cat", "head", "tail", "grep", "rg", "find", "pwd", "echo", "which", "type"}
)
GIT_READ_SUBCOMMANDS = frozenset(
    {"status", "log", "diff", "show", "branch", "remote", "ls-files", "rev-parse"}
)
SEGMENT_SPLIT_PATTERN = re.compile(r"[;&|]+")
PATH_TOKEN_PATTERN = re.compile(r"[A-Za-z]:[\\/][^\s'\"]+|/[^\s'\"]+")


def segment_is_read_only(segment: str) -> bool:
    words = segment.strip().split()
    if not words:
        return True
    first = words[0]
    if first == "git":
        subcommand = next((word for word in words[1:] if not word.startswith("-")), "")
        return subcommand in GIT_READ_SUBCOMMANDS
    return first in READ_ONLY_COMMANDS


def command_is_read_only(command: str) -> bool:
    return all(
        segment_is_read_only(segment) for segment in SEGMENT_SPLIT_PATTERN.split(command)
    )


def registry_lock_roots() -> list[tuple[str, dict]]:
    registry_directory = state_directory() / "locks"
    if not registry_directory.exists():
        return []
    roots: list[tuple[str, dict]] = []
    for entry in registry_directory.glob("*.json"):
        metadata = valid_metadata(read_json(entry))
        if metadata is None:
            continue
        root = metadata.get("root", "")
        if root and (Path(root) / MARKER_DIRECTORY_NAME).exists():
            roots.append((root, metadata))
    return roots


def check_bash(payload: dict) -> tuple[int, str]:
    command = payload.get("tool_input", {}).get("command", "")
    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd") or "."
    governed = governing_lock(cwd)
    if (
        governed is not None
        and lock_is_foreign(governed["lock"], session_id)
        and not command_is_read_only(command)
    ):
        return DENY, describe_lock(governed)
    for root, metadata in registry_lock_roots():
        if not lock_is_foreign(metadata, session_id):
            continue
        prefix = root.rstrip("\\/") + os.sep
        for token in PATH_TOKEN_PATTERN.findall(command):
            normalized = os.path.normcase(os.path.normpath(token))
            if normalized == root or normalized.startswith(prefix):
                return DENY, describe_lock({"root": root, "lock": metadata})
    return ALLOW, ""
```

Wire into `evaluate`:

```python
    if tool_name == "Bash":
        return check_bash(payload)
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/pre_tool_use.py tests/test_hook.py
git commit -m "feat: bounded Bash enforcement (cwd jurisdiction + path tokens)"
```

## Phase 4: Packaging, docs, gates

### Task 6: Ship the hook and document enforcement

**Files:**
- Create: `.skillpack` (single line: `hooks/`, plus a comment)
- Modify: `SKILL.md`
- Modify: `README.md` (enforcement summary + settings wiring)
- Modify: `AGENTS.md` (add hook test command to Commands section)

**Interfaces:**
- Consumes: everything shipped in Tasks 2-5.

- [ ] **Step 1: Create `.skillpack`**

```
# extra top-level entries shipped by install-skills
hooks/
```

- [ ] **Step 2: SKILL.md - add Enforcement section and jurisdiction rules**

After the "Non-negotiable rule" section add:

```markdown
## Enforcement

Installations may wire `hooks/pre_tool_use.py` as a Claude Code `PreToolUse`
hook (matcher `Edit|Write|NotebookEdit|Bash`). It denies file edits governed
by another session's lock and denies unlocked edits with the exact acquire
command to run (including `--session`). Bash calls are denied only when the
session's cwd sits in a foreign jurisdiction and the command is not read-only,
or when the command references an absolute path under a registered foreign
lock. Modes: `PROJECT_LOCK_ENFORCE=deny` (default), `warn`, `off`. The hook
fails open on its own errors.

Enforcement does not cover Bash writes that reach a foreign jurisdiction from
outside via relative or quoted paths. Think before you Bash: hold the locks of
every jurisdiction your command spans, especially for recursive operations
(`rm -rf`, `git clean`, formatter sweeps, `git add -A`).
```

In "Protocol and limitations" add:

```markdown
Jurisdiction is the nearest enclosing Git worktree: a lock governs everything
under its root except nested worktrees (their `.git` boundary stops it), and
an ancestor checkout's lock never reaches inside a worktree checked out as a
subfolder. `check` reports related ancestor and nested locks so subtree-wide
operations can be cleared manually first.
```

- [ ] **Step 3: README.md and AGENTS.md**

README: add an "Enforcement hook" subsection showing the settings wiring
(portable form, no machine paths):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.claude/skills/project-lock/hooks/pre_tool_use.py\""
          }
        ]
      }
    ]
  }
}
```

with a note that Windows settings use `%USERPROFILE%` semantics via
`$env:USERPROFILE` expansion or an absolute per-machine path in the user's
own settings file (settings are per-machine config, not shipped files).

AGENTS.md Commands block: add `python -m pytest tests/test_hook.py -q`.

- [ ] **Step 4: Verify installer picks up hooks/**

Run from the skills-dev umbrella root: `./install-skills.sh -n project-lock`
(or `install-skills.bat -n project-lock` on Windows)
Expected: dry-run lists `hooks/pre_tool_use.py` among shipped files.

- [ ] **Step 5: Commit**

```bash
git add .skillpack SKILL.md README.md AGENTS.md
git commit -m "docs: ship enforcement hook via skillpack and document wiring"
```

### Task 7: Gates, report, smoke

**Files:**
- Modify: `TEST-REPORT.md`
- Modify: `docs/superpowers/plans/2026-07-26-hook-enforcement.md` (prune)

- [ ] **Step 1: Run all gates**

```bash
python -m pytest -q
ruff check scripts tests hooks
ruff format --check scripts tests hooks
uvx --from skills-ref==0.1.1 agentskills validate ../project-lock
aislop ci .
```

Expected: all pass / score 100. If ruff's config does not cover `hooks/`, add
the directory to the ruff invocation targets in CI workflow files
(`.gitea/workflows/lint.yml`, `.github/workflows/lint.yml`) and AGENTS.md in
the same commit.

- [ ] **Step 2: Refresh TEST-REPORT.md**

Follow the existing file's format: date, command outputs, coverage numbers.

- [ ] **Step 3: Docs drift check**

Run the docs-update pass over README, SKILL.md, AGENTS.md: every behavioral
claim must match shipped behavior (especially: enforcement modes, allowlist
contents, jurisdiction rule, fail-open policy).

- [ ] **Step 4: Smoke test per SMOKE.md**

Follow `SMOKE.md`, plus one real-harness smoke: wire the hook into a throwaway
project's `.claude/settings.json`, acquire a lock with a fake session id from
a terminal, and confirm a live Claude Code session gets denied with the recipe
message, then allowed after acquiring.

- [ ] **Step 5: Commit and prune**

```bash
git add TEST-REPORT.md docs/superpowers/plans/2026-07-26-hook-enforcement.md
git commit -m "test: refresh TEST-REPORT for hook enforcement"
```

Branch-finish (separate step, per finishing-a-development-branch): fold
durable insight into README/SKILL.md, delete this plan, push to BOTH remotes
(`origin` = GitHub primary, `gitea` mirror), bump the skills-dev submodule
pointer on a skills-dev commit, and reinstall the skill locally
(`install-skills` from the umbrella) so the live copy under the user skills
directory picks up `hooks/`.
