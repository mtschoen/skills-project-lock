from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

import pytest
from project_lock import cli, core


def test_parse_duration() -> None:
    assert cli.parse_duration("2h") == timedelta(hours=2)
    for value, message in (
        ("", "required"),
        ("1", "end in"),
        ("xm", "numeric"),
        ("0s", "positive"),
    ):
        with pytest.raises(argparse.ArgumentTypeError, match=message):
            cli.parse_duration(value)


def test_cli_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    arguments = argparse.Namespace(
        path=tmp_path,
        reason="test",
        duration=timedelta(minutes=1),
        strategy="auto",
        owner="agent",
        session="session",
        owner_pid=None,
        json=False,
    )
    assert cli.command_acquire(arguments) == 0
    lock_id = core.inspect(tmp_path)["lock"]["lock_id"]
    assert "LOCKED" in capsys.readouterr().out

    assert cli.command_acquire(arguments) == cli.EXIT_LOCKED
    assert "project is locked" in capsys.readouterr().err

    check_arguments = argparse.Namespace(
        path=tmp_path, wait_threshold=timedelta(minutes=5), json=True
    )
    assert cli.command_check(check_arguments) == cli.EXIT_LOCKED
    assert json.loads(capsys.readouterr().out)["locked"]

    renew_arguments = argparse.Namespace(
        path=tmp_path,
        lock_id=lock_id,
        duration=timedelta(minutes=2),
        json=False,
    )
    assert cli.command_renew(renew_arguments) == 0
    assert "RENEWED" in capsys.readouterr().out
    renew_arguments.lock_id = "wrong"
    assert cli.command_renew(renew_arguments) == cli.EXIT_LOCKED
    assert "does not match" in capsys.readouterr().err

    release_arguments = argparse.Namespace(
        path=tmp_path, lock_id="wrong", force=False, expect_lock_id=None, reason=None
    )
    assert cli.command_release(release_arguments) == cli.EXIT_LOCKED
    release_arguments.lock_id = lock_id
    assert cli.command_release(release_arguments) == 0
    assert cli.command_release(release_arguments) == 0
    assert "FREE" in capsys.readouterr().out

    check_arguments.json = False
    assert cli.command_check(check_arguments) == 0
    assert "FREE" in capsys.readouterr().out
    assert cli.command_list(argparse.Namespace(json=False)) == 0
    assert "No project locks" in capsys.readouterr().out


def test_json_commands_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    arguments = argparse.Namespace(
        path=tmp_path,
        reason="test",
        duration=timedelta(minutes=1),
        strategy="auto",
        owner="agent",
        session=None,
        owner_pid=None,
        json=True,
    )
    assert cli.command_acquire(arguments) == 0
    metadata = json.loads(capsys.readouterr().out)

    renew_arguments = argparse.Namespace(
        path=tmp_path,
        lock_id=metadata["lock_id"],
        duration=timedelta(minutes=2),
        json=True,
    )
    assert cli.command_renew(renew_arguments) == 0
    assert json.loads(capsys.readouterr().out)["lock_id"] == metadata["lock_id"]

    assert cli.command_list(argparse.Namespace(json=True)) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert cli.command_list(argparse.Namespace(json=False)) == 0
    assert "owner:" in capsys.readouterr().out


def test_wait_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PROJECT_LOCK_STATE_DIR", str(tmp_path / "state"))
    arguments = argparse.Namespace(
        path=tmp_path,
        timeout=timedelta(seconds=0),
        wait_threshold=timedelta(minutes=5),
        interval=0,
    )
    assert cli.command_wait(arguments) == 0
    metadata = core.acquire(tmp_path, reason="busy", duration=timedelta(minutes=1))
    assert cli.command_wait(arguments) == cli.EXIT_LOCKED
    assert "Waiting for" not in capsys.readouterr().err

    calls = 0

    def inspect_then_release(path: Path, *, wait_threshold: timedelta) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return core.inspect(path, wait_threshold=wait_threshold)
        core.release(path, lock_id=metadata["lock_id"])
        return core.inspect(path, wait_threshold=wait_threshold)

    monkeypatch.setattr(cli, "inspect", inspect_then_release)
    arguments.timeout = timedelta(seconds=1)
    assert cli.command_wait(arguments) == 0
    assert "Waiting for" in capsys.readouterr().err


def test_parser_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["check", "."]).command == "check"
    monkeypatch.setattr(cli, "build_parser", lambda: parser)
    monkeypatch.setattr("sys.argv", ["project-lock.py", "list", "--json"])
    assert cli.main() == 0


def test_check_prints_related_nested_lock(nested_worktree_repo, capsys):
    nested = nested_worktree_repo["nested"]
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    check_arguments = argparse.Namespace(
        path=nested_worktree_repo["main"], wait_threshold=timedelta(minutes=5), json=False
    )
    exit_code = cli.command_check(check_arguments)
    output = capsys.readouterr().out
    assert "related: nested" in output
    assert exit_code == 0


def test_check_prints_related_ancestor_and_descendant_when_locked(nested_worktree_repo, capsys):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(main, reason="parent", duration=timedelta(minutes=5))
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))

    check_main = argparse.Namespace(path=main, wait_threshold=timedelta(minutes=5), json=False)
    assert cli.command_check(check_main) == cli.EXIT_LOCKED
    main_output = capsys.readouterr().out
    assert "related: nested" in main_output

    check_nested = argparse.Namespace(path=nested, wait_threshold=timedelta(minutes=5), json=False)
    assert cli.command_check(check_nested) == cli.EXIT_LOCKED
    nested_output = capsys.readouterr().out
    assert "related: ancestor" in nested_output


def test_check_prints_related_ancestor_when_free(nested_worktree_repo, capsys):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(main, reason="parent", duration=timedelta(minutes=5))
    check_nested = argparse.Namespace(path=nested, wait_threshold=timedelta(minutes=5), json=False)
    exit_code = cli.command_check(check_nested)
    output = capsys.readouterr().out
    assert "related: ancestor" in output
    assert exit_code == 0


def test_check_prints_related_for_damaged_metadata(nested_worktree_repo, capsys):
    main = nested_worktree_repo["main"]
    nested = nested_worktree_repo["nested"]
    core.acquire(nested, reason="child", duration=timedelta(minutes=5))
    (main / core.MARKER_DIRECTORY_NAME).mkdir()
    check_arguments = argparse.Namespace(path=main, wait_threshold=timedelta(minutes=5), json=False)
    exit_code = cli.command_check(check_arguments)
    output = capsys.readouterr().out
    assert "related: nested" in output
    assert exit_code == cli.EXIT_LOCKED


def test_watch_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    system_calls: list[str] = []
    monkeypatch.setattr(cli.os, "system", system_calls.append)
    cli.clear_screen()
    assert system_calls
    monkeypatch.setattr(cli, "clear_screen", lambda: None)
    monkeypatch.setattr(cli, "command_list", lambda _: 0)
    monkeypatch.setattr(cli.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
    assert cli.command_watch(argparse.Namespace(interval=0)) == 0
