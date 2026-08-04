"""Cooperative project locking for coding agents."""

from .core import (
    LockConflict,
    LockOwnershipError,
    acquire,
    governing_lock,
    has_git_ancestor,
    inspect,
    is_under_temp_dir,
    list_locks,
    nearest_worktree_root,
    related_locks,
    release,
    renew,
)

__all__ = [
    "LockConflict",
    "LockOwnershipError",
    "acquire",
    "governing_lock",
    "has_git_ancestor",
    "inspect",
    "is_under_temp_dir",
    "list_locks",
    "nearest_worktree_root",
    "related_locks",
    "release",
    "renew",
]
