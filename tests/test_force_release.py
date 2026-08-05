from __future__ import annotations

import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from project_lock import audit, core


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    return root


def hold(repository: Path) -> dict:
    return core.acquire(
        repository,
        reason="busy",
        duration=timedelta(minutes=5),
        strategy="wait",
        owner="other-agent",
    )


def test_force_requires_a_reason(repository: Path) -> None:
    metadata = hold(repository)

    with pytest.raises(core.LockOwnershipError, match="reason"):
        core.release(repository, force=True, expect_lock_id=metadata["lock_id"])

    assert core.inspect(repository)["locked"]


def test_force_requires_the_expected_lock_id(repository: Path) -> None:
    hold(repository)

    with pytest.raises(core.LockOwnershipError, match="--expect-lock-id"):
        core.release(repository, force=True, reason="owner session is abandoned")

    assert core.inspect(repository)["locked"]


def test_force_rejects_a_stale_expected_lock_id(repository: Path) -> None:
    """The lock verified as abandoned must be the lock actually cleared."""
    first = hold(repository)
    core.release(repository, lock_id=first["lock_id"])
    replacement = hold(repository)

    with pytest.raises(core.LockOwnershipError, match="no longer holds"):
        core.release(repository, force=True, reason="stale check", expect_lock_id=first["lock_id"])

    assert core.inspect(repository)["lock"]["lock_id"] == replacement["lock_id"]


def test_force_clears_with_matching_expectation_and_reason(repository: Path) -> None:
    metadata = hold(repository)

    assert core.release(
        repository,
        force=True,
        reason="verified the owner session exited",
        expect_lock_id=metadata["lock_id"],
    )
    assert not core.inspect(repository)["locked"]


def test_force_clear_is_audited(repository: Path) -> None:
    metadata = hold(repository)

    core.release(
        repository,
        force=True,
        reason="verified the owner session exited",
        expect_lock_id=metadata["lock_id"],
    )

    records = [json.loads(line) for line in audit.audit_path().read_text().splitlines() if line]
    assert len(records) == 1
    assert records[0]["action"] == "force-release"
    assert records[0]["cleared_lock_id"] == metadata["lock_id"]
    assert records[0]["reason"] == "verified the owner session exited"
    assert records[0]["owner"] == "other-agent"
    assert records[0]["root"] == str(core.resolve_root(repository))
    assert records[0]["at"]


def test_force_clears_unreadable_metadata_without_an_expectation(repository: Path) -> None:
    """Corrupt metadata has no lock id to compare, so a reason is the whole gate."""
    hold(repository)
    core.metadata_path(core.resolve_root(repository)).write_text("{ broken", encoding="utf-8")

    assert core.release(repository, force=True, reason="metadata corrupt, owner gone")
    assert not core.inspect(repository)["locked"]

    records = [json.loads(line) for line in audit.audit_path().read_text().splitlines() if line]
    assert records[0]["cleared_lock_id"] is None


def test_unreadable_metadata_still_requires_a_reason(repository: Path) -> None:
    hold(repository)
    core.metadata_path(core.resolve_root(repository)).write_text("{ broken", encoding="utf-8")

    with pytest.raises(core.LockOwnershipError, match="reason"):
        core.release(repository, force=True)


def test_releasing_a_free_path_stays_a_no_op(repository: Path) -> None:
    assert not core.release(repository, force=True)
    assert not audit.audit_path().exists()


def test_owner_release_is_unaffected(repository: Path) -> None:
    metadata = hold(repository)

    assert core.release(repository, lock_id=metadata["lock_id"])
    assert not audit.audit_path().exists()


def test_acquire_records_no_owner_process_by_default(repository: Path) -> None:
    metadata = hold(repository)

    assert metadata["owner_pid"] is None
    assert metadata["owner_process_start"] is None
    assert core.inspect(repository)["owner_process"] == "unknown"


def test_acquire_records_a_nominated_owner_process(repository: Path) -> None:
    metadata = core.acquire(
        repository,
        reason="busy",
        duration=timedelta(minutes=5),
        owner_pid=os.getpid(),
    )

    assert metadata["owner_pid"] == os.getpid()
    assert core.inspect(repository)["owner_process"] in {"running", "running-unverified"}
