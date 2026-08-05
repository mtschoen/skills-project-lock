from __future__ import annotations

from pathlib import Path

from project_lock import git_admin


def test_common_git_directory_is_admin_state(nested_worktree_repo) -> None:
    main = nested_worktree_repo["main"]

    assert git_admin.is_git_admin_path(main / ".git" / "config")
    assert git_admin.is_git_admin_path(main / ".git" / "HEAD")
    assert git_admin.is_git_admin_path(main / ".git" / "refs" / "heads" / "main")


def test_worktree_content_is_not_admin_state(nested_worktree_repo) -> None:
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]

    assert not git_admin.is_git_admin_path(main / "README.md")
    assert not git_admin.is_git_admin_path(main / "src" / "deep" / "new.py")
    assert not git_admin.is_git_admin_path(nested / "README.md")


def test_private_worktree_metadata_is_admin_state(nested_worktree_repo) -> None:
    main = nested_worktree_repo["main"]
    target = main / ".git" / "worktrees" / "sibling" / "HEAD"

    assert git_admin.is_git_admin_path(target)


def test_linked_worktree_git_marker_file_is_admin_state(nested_worktree_repo) -> None:
    """The `.git` *file* at a linked worktree root repoints the worktree."""
    sibling = nested_worktree_repo["sibling"]

    assert (sibling / ".git").is_file()
    assert git_admin.is_git_admin_path(sibling / ".git")


def test_missing_file_under_git_directory_is_admin_state(nested_worktree_repo) -> None:
    main = nested_worktree_repo["main"]

    assert git_admin.is_git_admin_path(main / ".git" / "hooks" / "post-commit")


def test_non_repository_path_is_not_admin_state(tmp_path: Path) -> None:
    plain = tmp_path / "notes"
    plain.mkdir()

    assert not git_admin.is_git_admin_path(plain / "todo.md")


def test_prefilter_avoids_git_subprocess_for_ordinary_paths(
    nested_worktree_repo, monkeypatch
) -> None:
    """No path component named `.git` means no subprocess cost on the hot path."""
    main = nested_worktree_repo["main"]

    def explode(*arguments, **keywords):
        raise AssertionError("git must not be invoked for an ordinary content path")

    monkeypatch.setattr(git_admin, "run_git", explode)

    assert not git_admin.is_git_admin_path(main / "src" / "app.py")


def test_missing_git_output_yields_no_administration_roots(nested_worktree_repo, monkeypatch):
    """Git unavailable or silent means the target is treated as ordinary content."""
    main = nested_worktree_repo["main"]
    monkeypatch.setattr(git_admin, "run_git", lambda *arguments: None)

    assert git_admin.git_administration_roots(main) == []


def test_relative_git_directory_resolves_against_the_worktree_root(
    nested_worktree_repo, monkeypatch
):
    main = nested_worktree_repo["main"]
    monkeypatch.setattr(git_admin, "run_git", lambda path, *arguments: ".git")

    roots = git_admin.git_administration_roots(main)

    assert roots == [git_admin.canonical_path(main / ".git")] * 2


def test_reported_but_absent_git_directory_is_skipped(nested_worktree_repo, monkeypatch):
    main = nested_worktree_repo["main"]
    monkeypatch.setattr(git_admin, "run_git", lambda path, *arguments: str(main / "no-such-dir"))

    assert git_admin.git_administration_roots(main) == []
