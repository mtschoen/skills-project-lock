from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from project_lock import core


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    return root


def test_acquire_inspect_renew_release(repository: Path) -> None:
    metadata = core.acquire(
        repository,
        reason="quick fix",
        duration=timedelta(minutes=2),
        strategy="auto",
        owner="test-agent",
        session="session-1",
    )

    assert metadata["owner"] == "test-agent"
    assert metadata["session"] == "session-1"
    assert metadata["platform"]
    assert core.inspect(repository)["recommendation"] == "wait, then check again"
    assert core.list_locks()[0]["lock"]["lock_id"] == metadata["lock_id"]
    assert "/.agent-lock/" in (repository / ".git" / "info" / "exclude").read_text()

    renewed = core.renew(repository, lock_id=metadata["lock_id"], duration=timedelta(hours=1))
    assert renewed["updated_at"] >= metadata["updated_at"]
    assert core.inspect(repository)["recommendation"] == "use a separate worktree"
    assert core.release(repository, lock_id=metadata["lock_id"])
    assert not core.release(repository, force=True)
    assert core.list_locks() == []


def test_explicit_strategies_and_overdue(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = core.acquire(
        repository,
        reason="main session",
        duration=timedelta(hours=1),
        strategy="wait",
    )
    assert core.inspect(repository)["recommendation"] == "wait for this worktree"

    core.release(repository, lock_id=metadata["lock_id"])
    metadata = core.acquire(
        repository,
        reason="main session",
        duration=timedelta(hours=1),
        strategy="worktree",
    )
    assert core.inspect(repository)["recommendation"] == "use a separate worktree"

    future = core.parse_time(metadata["expected_until"]) + timedelta(seconds=1)
    monkeypatch.setattr(core, "utc_now", lambda: future)
    status = core.inspect(repository)
    assert status["overdue"]
    assert status["recommendation"].startswith("overdue:")


def test_conflict_and_ownership_errors(repository: Path) -> None:
    metadata = core.acquire(
        repository,
        reason="first",
        duration=timedelta(minutes=1),
    )
    with pytest.raises(core.LockConflict) as conflict:
        core.acquire(repository, reason="second", duration=timedelta(minutes=1))
    assert conflict.value.status["lock"]["reason"] == "first"

    with pytest.raises(core.LockOwnershipError, match="does not match"):
        core.renew(repository, lock_id="wrong", duration=timedelta(minutes=1))
    with pytest.raises(core.LockOwnershipError, match="required"):
        core.release(repository)
    with pytest.raises(core.LockOwnershipError, match="does not match"):
        core.release(repository, lock_id="wrong")
    assert core.release(repository, force=True)
    assert metadata["lock_id"]


def test_corrupt_metadata_requires_force(repository: Path) -> None:
    marker = repository / core.MARKER_DIRECTORY_NAME
    marker.mkdir()
    metadata_file = marker / core.METADATA_FILE_NAME
    metadata_file.write_text('{"version": 1, "lock_id": "partial"}')
    status = core.inspect(repository)
    assert status["locked"]
    assert status["lock"]["owner"] == "unknown"

    with pytest.raises(core.LockOwnershipError, match="metadata is unavailable"):
        core.release(repository)
    unexpected = marker / "unexpected"
    unexpected.write_text("x")
    with pytest.raises(core.LockOwnershipError, match="unexpected"):
        core.release(repository, force=True)
    assert metadata_file.exists()
    unexpected.unlink()
    assert core.release(repository, force=True)


def test_non_git_root_and_file_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    project = tmp_path / "plain"
    project.mkdir()
    file_path = project / "file.txt"
    file_path.write_text("content")

    metadata = core.acquire(file_path, reason="plain", duration=timedelta(seconds=1))
    assert Path(metadata["root"]) == core.canonical_path(project)
    assert core.release(project, lock_id=metadata["lock_id"])
    with pytest.raises(FileNotFoundError):
        core.inspect(tmp_path / "missing")


def test_registry_cleanup(repository: Path) -> None:
    registry_directory = core.state_directory() / "locks"
    registry_directory.mkdir(parents=True)
    corrupt = registry_directory / "corrupt.json"
    corrupt.write_text("not-json")
    missing = registry_directory / "missing.json"
    missing.write_text(json.dumps({"root": str(repository.parent / "gone")}))
    free = registry_directory / "free.json"
    free.write_text(json.dumps({"root": str(repository)}))

    assert core.list_locks() == []
    assert corrupt.exists()
    assert not missing.exists()
    assert not free.exists()


def test_helpers_and_failure_cleanup(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert core.format_time(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00Z"
    assert core.default_owner()
    assert core.current_branch(repository) == "main"

    monkeypatch.setattr(core, "atomic_write_json", lambda *_: (_ for _ in ()).throw(OSError("no")))
    with pytest.raises(OSError, match="no"):
        core.acquire(repository, reason="failure", duration=timedelta(seconds=1))
    assert not (repository / core.MARKER_DIRECTORY_NAME).exists()


def test_state_directory_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROJECT_LOCK_STATE_DIR", raising=False)
    monkeypatch.setattr(core.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert core.state_directory() == tmp_path / "local" / "project-lock"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "windows-fallback"))
    assert core.state_directory() == tmp_path / "windows-fallback" / "project-lock"
    monkeypatch.setattr(core.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert core.state_directory() == tmp_path / "project-lock"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setattr(core.Path, "home", lambda: tmp_path)
    assert core.state_directory() == tmp_path / ".local" / "state" / "project-lock"


def test_valid_metadata_rejects_invalid_shapes(repository: Path) -> None:
    metadata = core.build_metadata(
        repository,
        reason="test",
        duration=timedelta(minutes=1),
        strategy="auto",
        owner="owner",
        session=None,
    )
    assert core.valid_metadata(metadata) is metadata
    assert core.valid_metadata(None) is None
    for change in (
        {"version": 0},
        {"owner": ""},
        {"owner": 1},
        {"strategy": "invalid"},
        {"expected_until": "not-a-time"},
        {"expected_until": "2026-01-01T00:00:00"},
    ):
        candidate = metadata | change
        assert core.valid_metadata(candidate) is None


def test_mutations_are_serialized(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = core.acquire(repository, reason="first", duration=timedelta(minutes=1))
    renewal_paused = threading.Event()
    continue_renewal = threading.Event()
    original_write = core.atomic_write_json

    def pausing_write(path: Path, payload: dict) -> None:
        if threading.current_thread().name == "renew" and path == core.metadata_path(repository):
            renewal_paused.set()
            assert continue_renewal.wait(timeout=5)
        original_write(path, payload)

    monkeypatch.setattr(core, "atomic_write_json", pausing_write)
    renew_thread = threading.Thread(
        name="renew",
        target=core.renew,
        kwargs={"path": repository, "lock_id": first["lock_id"], "duration": timedelta(minutes=2)},
    )
    replacement: dict = {}

    def replace_lock() -> None:
        core.release(repository, lock_id=first["lock_id"])
        replacement.update(core.acquire(repository, reason="second", duration=timedelta(minutes=1)))

    replace_thread = threading.Thread(name="replace", target=replace_lock)
    renew_thread.start()
    assert renewal_paused.wait(timeout=5)
    replace_thread.start()
    assert replace_thread.is_alive()
    continue_renewal.set()
    renew_thread.join(timeout=5)
    replace_thread.join(timeout=5)
    assert not renew_thread.is_alive()
    assert not replace_thread.is_alive()
    assert core.inspect(repository)["lock"]["lock_id"] == replacement["lock_id"]


def test_release_failure_preserves_metadata(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = core.acquire(repository, reason="owner evidence", duration=timedelta(minutes=1))
    original_remove_directory = core.Path.rmdir

    def fail_for_marker(path: Path) -> None:
        if path == core.marker_directory(repository):
            raise PermissionError("simulated")
        original_remove_directory(path)

    monkeypatch.setattr(core.Path, "rmdir", fail_for_marker)
    with pytest.raises(core.LockOwnershipError, match="could not remove"):
        core.release(repository, lock_id=metadata["lock_id"])
    assert core.inspect(repository)["lock"]["owner"] == metadata["owner"]

    monkeypatch.setattr(core.Path, "rmdir", original_remove_directory)
    assert core.release(repository, lock_id=metadata["lock_id"])
    core.marker_directory(repository).mkdir()
    monkeypatch.setattr(core.Path, "rmdir", fail_for_marker)
    with pytest.raises(core.LockOwnershipError, match="could not remove"):
        core.release(repository, force=True)


def test_ignore_and_missing_renew_paths(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exclude = repository / ".git" / "info" / "exclude"
    exclude.write_text("existing-without-newline")
    core.ensure_git_ignore(repository)
    first = exclude.read_text()
    assert first == "existing-without-newline\n/.agent-lock/\n"
    core.ensure_git_ignore(repository)
    assert exclude.read_text() == first

    monkeypatch.setattr(core, "run_git", lambda *_: str(repository / ".git"))
    core.ensure_git_ignore(repository)
    with pytest.raises(core.LockOwnershipError, match="not locked"):
        core.renew(repository, lock_id="none", duration=timedelta(seconds=1))
