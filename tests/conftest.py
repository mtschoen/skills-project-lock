from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))


@pytest.fixture(autouse=True)
def isolated_state_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
