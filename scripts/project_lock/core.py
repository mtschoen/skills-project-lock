"""Atomic, inspectable project-lock protocol."""

# aislop-ignore-file complexity/file-too-large -- cohesive protocol core; split tracked separately

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MARKER_DIRECTORY_NAME = ".agent-lock"
METADATA_FILE_NAME = "owner.json"


class LockConflict(RuntimeError):
    """Raised when another owner already holds the project lock."""

    def __init__(self, status: dict[str, Any]):
        super().__init__(f"project is locked: {status['root']}")
        self.status = status


class LockOwnershipError(RuntimeError):
    """Raised when a caller cannot prove ownership of a lock."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_path(path: Path) -> Path:
    return Path(os.path.normcase(str(path.resolve())))


def run_git(path: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip()


def resolve_root(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise FileNotFoundError(f"path does not exist: {candidate}")
    if candidate.is_file():
        candidate = candidate.parent
    git_root = run_git(candidate, "rev-parse", "--show-toplevel")
    return canonical_path(Path(git_root) if git_root else candidate)


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


def has_git_ancestor(path: Path | str) -> bool:
    """True if `path` (or an existing ancestor) sits inside a Git working tree.

    Used by the pre-write hook to tell a real, unlocked project apart from
    scratch space that never had a project root to begin with.
    """
    start = deepest_existing_directory(path)
    current = start
    while True:
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _temp_roots() -> list[Path]:
    """Recognized system scratch roots, canonicalized and de-duplicated.

    Covers `tempfile.gettempdir()`, `$TMPDIR`, and, off Windows, the common
    Unix/macOS temp mounts. Candidates that don't exist on this machine
    (e.g. `/var/folders` off macOS) are skipped. Resolving each candidate
    means a symlinked mount (macOS `/tmp` -> `/private/tmp`) matches paths
    that reach it through either name.

    The POSIX literals are gated on `os.name != "nt"`: on Windows they are
    drive-relative, not absolute, so `/tmp` resolves against the current
    working directory's drive (e.g. `C:\\tmp`) rather than denoting "no
    recognized temp location". Probing them there would enroll whatever
    happens to exist at `<cwd drive>:\\tmp` as a scratch root, and the
    result would depend on the caller's current drive.
    """
    candidates = [
        tempfile.gettempdir(),
        os.environ.get("TMPDIR"),
    ]
    if os.name != "nt":
        candidates += ["/tmp", "/private/tmp", "/var/folders"]
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.exists():
            continue
        resolved = canonical_path(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)
    return roots


def is_under_temp_dir(path: Path | str) -> bool:
    """True if `path` (or its deepest existing ancestor) resolves under a
    recognized system temp location.

    Paired with `has_git_ancestor()` to scope the hook's unlocked-write
    carve-out to true scratch space: a Git checkout that happens to live
    under a temp mount (e.g. a test fixture under `/tmp`) is still enforced,
    because `has_git_ancestor()` catches that case first.
    """
    target = deepest_existing_directory(path)
    return any(target == root or root in target.parents for root in _temp_roots())


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


def current_branch(root: Path) -> str | None:
    return run_git(root, "branch", "--show-current") or None


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


def registry_path(root: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return state_directory() / "locks" / f"{digest}.json"


def marker_directory(root: Path) -> Path:
    return root / MARKER_DIRECTORY_NAME


def metadata_path(root: Path) -> Path:
    return marker_directory(root) / METADATA_FILE_NAME


@contextmanager
def mutation_guard():
    """Serialize lock mutations for agents running as the current user."""
    database_path = state_directory() / "coordination.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
    finally:
        connection.close()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def valid_metadata(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None or payload.get("version") != PROTOCOL_VERSION:
        return None
    required_strings = (
        "lock_id",
        "root",
        "owner",
        "reason",
        "created_at",
        "updated_at",
        "expected_until",
    )
    invalid_required_field = any(
        not isinstance(payload.get(field), str) or not payload[field] for field in required_strings
    )
    if invalid_required_field:
        return None
    if payload.get("strategy") not in {"auto", "wait", "worktree"}:
        return None
    try:
        timestamps = (
            parse_time(payload["created_at"]),
            parse_time(payload["updated_at"]),
            parse_time(payload["expected_until"]),
        )
    except ValueError:
        return None
    if any(timestamp.utcoffset() is None for timestamp in timestamps):
        return None
    return payload


def ensure_git_ignore(root: Path) -> None:
    git_directory = run_git(root, "rev-parse", "--git-common-dir")
    if not git_directory:
        return
    git_path = Path(git_directory)
    if not git_path.is_absolute():
        git_path = root / git_path
    exclude_path = git_path.resolve() / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    pattern = f"/{MARKER_DIRECTORY_NAME}/"
    if pattern not in existing.splitlines():
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{prefix}{pattern}\n")


def default_owner() -> str:
    agent = os.environ.get("AGENT_NAME") or os.environ.get("USER") or os.environ.get("USERNAME")
    return f"{agent or 'agent'}@{socket.gethostname()}"


def build_metadata(
    root: Path,
    *,
    reason: str,
    duration: timedelta,
    strategy: str,
    owner: str | None,
    session: str | None,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "version": PROTOCOL_VERSION,
        "lock_id": str(uuid.uuid4()),
        "root": str(root),
        "owner": owner or default_owner(),
        "session": session
        or os.environ.get("CLAUDE_SESSION_ID")
        or os.environ.get("PI_SESSION_ID"),
        "reason": reason,
        "strategy": strategy,
        "branch": current_branch(root),
        "host": socket.gethostname(),
        "platform": platform.system(),
        "creator_pid": os.getpid(),
        "created_at": format_time(now),
        "updated_at": format_time(now),
        "expected_until": format_time(now + duration),
    }


def acquire(
    path: Path | str,
    *,
    reason: str,
    duration: timedelta,
    strategy: str = "auto",
    owner: str | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    root = resolve_root(path)
    marker = marker_directory(root)
    with mutation_guard():
        try:
            marker.mkdir()
        except FileExistsError as error:
            raise LockConflict(inspect(root)) from error

        metadata = build_metadata(
            root,
            reason=reason,
            duration=duration,
            strategy=strategy,
            owner=owner,
            session=session,
        )
        try:
            atomic_write_json(metadata_path(root), metadata)
            atomic_write_json(registry_path(root), metadata)
            ensure_git_ignore(root)
        except Exception:
            metadata_path(root).unlink(missing_ok=True)
            marker.rmdir()
            raise
        return metadata


def recommendation(metadata: dict[str, Any], *, now: datetime, wait_threshold: timedelta) -> str:
    if now > parse_time(metadata["expected_until"]):
        return "overdue: contact the owner; force-clear only after verifying it is abandoned"
    strategy = metadata.get("strategy", "auto")
    if strategy == "wait":
        return "wait for this worktree"
    if strategy == "worktree":
        return "use a separate worktree"
    remaining = parse_time(metadata["expected_until"]) - now
    if remaining <= wait_threshold:
        return "wait, then check again"
    return "use a separate worktree"


def inspect(
    path: Path | str, *, wait_threshold: timedelta = timedelta(minutes=5)
) -> dict[str, Any]:
    root = resolve_root(path)
    marker = marker_directory(root)
    if not marker.exists():
        status: dict[str, Any] = {"locked": False, "root": str(root)}
        related = related_locks(root)
        if related["ancestors"] or related["descendants"]:
            status["related"] = related
        return status
    metadata = valid_metadata(read_json(metadata_path(root)))
    if metadata is None:
        damaged_status: dict[str, Any] = {
            "locked": True,
            "root": str(root),
            "overdue": False,
            "recommendation": "lock is initializing or damaged; wait and inspect manually",
            "lock": {
                "lock_id": "unknown",
                "owner": "unknown",
                "reason": "metadata unavailable",
                "expected_until": "unknown",
            },
        }
        related = related_locks(root)
        if related["ancestors"] or related["descendants"]:
            damaged_status["related"] = related
        return damaged_status
    now = utc_now()
    status = {
        "locked": True,
        "root": str(root),
        "overdue": now > parse_time(metadata["expected_until"]),
        "recommendation": recommendation(metadata, now=now, wait_threshold=wait_threshold),
        "lock": metadata,
    }
    related = related_locks(root)
    if related["ancestors"] or related["descendants"]:
        status["related"] = related
    return status


def verify_owner(metadata: dict[str, Any], lock_id: str | None, force: bool) -> None:
    if force:
        return
    if not lock_id:
        raise LockOwnershipError("--lock-id is required unless --force is used")
    if metadata.get("lock_id") != lock_id:
        raise LockOwnershipError("lock id does not match the current owner")


def renew(path: Path | str, *, lock_id: str, duration: timedelta) -> dict[str, Any]:
    root = resolve_root(path)
    with mutation_guard():
        metadata = valid_metadata(read_json(metadata_path(root)))
        if metadata is None:
            raise LockOwnershipError("project is not locked or lock metadata is unavailable")
        verify_owner(metadata, lock_id, False)
        now = utc_now()
        metadata["updated_at"] = format_time(now)
        metadata["expected_until"] = format_time(now + duration)
        atomic_write_json(metadata_path(root), metadata)
        atomic_write_json(registry_path(root), metadata)
        return metadata


def release(path: Path | str, *, lock_id: str | None = None, force: bool = False) -> bool:
    root = resolve_root(path)
    marker = marker_directory(root)
    with mutation_guard():
        if not marker.exists():
            registry_path(root).unlink(missing_ok=True)
            return False
        raw_metadata = read_json(metadata_path(root))
        metadata = valid_metadata(raw_metadata)
        if metadata is None and not force:
            raise LockOwnershipError(
                "lock metadata is unavailable; use --force after verifying abandonment"
            )
        if metadata is not None:
            verify_owner(metadata, lock_id, force)
        unexpected_entries = {entry.name for entry in marker.iterdir()} - {METADATA_FILE_NAME}
        if unexpected_entries:
            names = ", ".join(sorted(unexpected_entries))
            raise LockOwnershipError(f"lock directory contains unexpected files: {names}")
        metadata_file = metadata_path(root)
        metadata_file.unlink(missing_ok=True)
        try:
            marker.rmdir()
        except OSError as error:
            if raw_metadata is not None:
                atomic_write_json(metadata_file, raw_metadata)
            raise LockOwnershipError(f"could not remove lock directory: {marker}") from error
        registry_path(root).unlink(missing_ok=True)
        return True


def list_locks() -> list[dict[str, Any]]:
    registry_directory = state_directory() / "locks"
    if not registry_directory.exists():
        return []
    statuses: list[dict[str, Any]] = []
    for entry in sorted(registry_directory.glob("*.json")):
        metadata = read_json(entry)
        if metadata is None or not metadata.get("root"):
            continue
        try:
            status = inspect(Path(metadata["root"]))
        except FileNotFoundError:
            entry.unlink(missing_ok=True)
            continue
        if status["locked"]:
            statuses.append(status)
        else:
            entry.unlink(missing_ok=True)
    return statuses
