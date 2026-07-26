"""Cooperative project locking for coding agents."""

from .core import (
    LockConflict,
    LockOwnershipError,
    acquire,
    governing_lock,
    inspect,
    list_locks,
    nearest_worktree_root,
    release,
    renew,
)

__all__ = [
    "LockConflict",
    "LockOwnershipError",
    "acquire",
    "governing_lock",
    "inspect",
    "list_locks",
    "nearest_worktree_root",
    "release",
    "renew",
]
