"""Ownership of Git administration state.

Refs, config and the worktree registry are shared by every worktree of a
repository, so an ordinary worktree lock cannot speak for them. Rather than
orphan that state, it is owned by the **governing checkout**: the checkout
whose `.git` the path belongs to, which for repository-wide state is the main
worktree. Holding that lock means being in charge of the repository.

Three outcomes follow, and there is deliberately no separate escape hatch:
acquiring the governing checkout's lock *is* the way in, which keeps the
authority explicit and visible to every other agent.

- Governing checkout locked by this session: allowed.
- Locked by another session: refused; that agent is in charge.
- Unlocked, and nothing anywhere in the repository is locked: allowed, since
  there is no one to coordinate with.
- Unlocked, but another worktree of the repository is locked: refused, so a
  session working in a linked worktree cannot rewrite shared state out from
  under whoever else is active without first claiming the main checkout.

This governs direct file-tool writes only. Bash is excluded: Git writes inside
`.git` on nearly every invocation, and a word-level classifier cannot separate
that from a raw clobber.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import (
    canonical_path,
    deepest_existing_directory,
    marker_directory,
    metadata_path,
    nearest_worktree_root,
    read_json,
    run_git,
    valid_metadata,
)

GIT_DIRECTORY_NAME = ".git"


def _may_be_git_administration(path: Path) -> bool:
    """Cheap pre-filter: no `.git`-ish component means no subprocess cost.

    Every administration path either lives under a Git directory or is the
    `.git` marker itself, so an ordinary content path is cleared without
    invoking Git. This keeps the pre-write hook off the subprocess path for the
    overwhelmingly common case. The `endswith` arm covers bare repositories
    (`project.git/config`), at the cost of a wasted probe on an ordinary file
    that happens to end in `.git`.
    """
    return any(part.endswith(GIT_DIRECTORY_NAME) for part in path.parts)


def git_administration_roots(worktree_root: Path) -> list[Path]:
    """Canonical Git directories governing `worktree_root`.

    Returns the private Git directory (for a linked worktree, its own
    `worktrees/<name>` subdirectory) and the common Git directory shared by
    every worktree of the repository. Either may be absent when Git is
    unavailable or the path is not a repository, in which case the caller
    treats the target as ordinary content.
    """
    roots: list[Path] = []
    for argument in ("--git-dir", "--git-common-dir"):
        reported = run_git(worktree_root, "rev-parse", argument)
        if not reported:
            continue
        candidate = Path(reported)
        if not candidate.is_absolute():
            candidate = worktree_root / candidate
        if candidate.exists():
            roots.append(canonical_path(candidate))
    return roots


def _is_worktree_git_marker(path: Path, worktree_root: Path) -> bool:
    """True for the `.git` entry at a worktree root.

    A directory for the main checkout, a file pointing at the private Git
    directory for a linked one. Rewriting that file repoints the worktree, so
    it is administration state even though it sits outside both Git directories.
    """
    return path.name == GIT_DIRECTORY_NAME and canonical_path(path.parent) == worktree_root


def governing_checkout(target: Path | str) -> Path:
    """The checkout that owns the Git administration state at `target`.

    This is the checkout whose `.git` entry the path belongs to. For
    repository-wide state (`config`, `refs/`, the `worktrees/` registry) that
    is the main worktree, because the common Git directory lives inside its
    `.git`. It is also correct for a submodule, whose Git directory sits under
    the superproject's `.git` and is therefore governed by the superproject.

    Derived by walking the filesystem rather than from `git worktree list`,
    whose first entry is documented as the main worktree but reports the Git
    directory instead of the working tree for a submodule.
    """
    return nearest_worktree_root(Path(target).expanduser())


def lock_at(root: Path) -> dict[str, Any] | None:
    """The lock held exactly at `root`, if any. Does not walk ancestors."""
    return valid_metadata(read_json(metadata_path(root)))


def repository_worktrees(checkout: Path) -> list[Path]:
    """Every registered worktree of `checkout`'s repository.

    Uses Git's own inventory rather than assuming worktrees are siblings or
    nested, since a linked worktree may live anywhere on disk. Entries that do
    not correspond to a real checkout (as reported for a submodule) are
    harmless here: they simply never carry a lock marker.
    """
    reported = run_git(checkout, "worktree", "list", "--porcelain", "-z")
    if not reported:
        return []
    roots: list[Path] = []
    for record in reported.split("\0"):
        if record.startswith("worktree "):
            roots.append(canonical_path(Path(record[len("worktree ") :])))
    return roots


def repository_is_idle(checkout: Path) -> bool:
    """True when no worktree of this repository carries a lock.

    With nobody working anywhere in the repository there is no one to
    coordinate with, so administration writes need no claim.
    """
    roots = repository_worktrees(checkout)
    if checkout not in roots:
        roots.append(checkout)
    return not any(marker_directory(root).exists() for root in roots)


def is_git_admin_path(target: Path | str) -> bool:
    """True if a direct write to `target` would mutate Git administration state."""
    path = Path(target).expanduser()
    if not _may_be_git_administration(path):
        return False
    worktree_root = nearest_worktree_root(path)
    if _is_worktree_git_marker(path, worktree_root):
        return True
    # Containment is decided on the deepest existing ancestor directory, which
    # is where the target would be created. A path whose own parents do not yet
    # exist cannot be inside a Git directory that already exists.
    existing = deepest_existing_directory(path)
    return any(
        existing == root or root in existing.parents
        for root in git_administration_roots(worktree_root)
    )
