from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))


@pytest.fixture(autouse=True)
def isolated_state_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))


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
