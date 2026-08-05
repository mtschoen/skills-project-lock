"""Classification of Git administration state.

A project lock's jurisdiction is *worktree content*. Git administration state
(refs, config, the worktree registry) is shared by every worktree of a
repository, so the lock at any one worktree root cannot speak for it: the main
worktree's holder would otherwise silently own state that a sibling worktree's
holder depends on. Direct file-tool writes to that state are refused outright
and routed through Git commands instead, which is the only vocabulary that
expresses them as repository operations.
"""

from __future__ import annotations

from pathlib import Path

from .core import canonical_path, deepest_existing_directory, nearest_worktree_root, run_git

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
