from __future__ import annotations

import os
import socket
import sys
from types import SimpleNamespace

import pytest
from project_lock import process_identity

SUPPORTED_PLATFORM = sys.platform.startswith("linux") or sys.platform == "win32"
# Above every platform's configured pid_max, so no live process can hold it.
UNASSIGNABLE_PID = 2**31 - 1


def test_current_process_is_running() -> None:
    state, identity = process_identity.probe_process(os.getpid())

    if SUPPORTED_PLATFORM:
        assert state == "running"
        assert identity is not None
        assert identity == process_identity.process_start_identity(os.getpid())
    else:
        assert state == "unknown"
        assert identity is None


@pytest.mark.skipif(not SUPPORTED_PLATFORM, reason="prober reports unknown off Linux/Windows")
def test_unassignable_pid_is_gone() -> None:
    state, identity = process_identity.probe_process(UNASSIGNABLE_PID)

    assert state == "gone"
    assert identity is None


def test_start_identity_is_none_for_unassignable_pid() -> None:
    assert process_identity.process_start_identity(UNASSIGNABLE_PID) is None


def test_owner_liveness_reports_unknown_for_a_different_host() -> None:
    metadata = {"host": "some-other-host", "owner_pid": os.getpid()}

    assert process_identity.owner_liveness(metadata) == "unknown"


def test_owner_liveness_reports_unknown_without_pid() -> None:
    metadata = {"host": socket.gethostname()}

    assert process_identity.owner_liveness(metadata) == "unknown"


def test_owner_liveness_reports_unknown_for_a_malformed_pid() -> None:
    metadata = {"host": socket.gethostname(), "owner_pid": "not-a-pid"}

    assert process_identity.owner_liveness(metadata) == "unknown"


def test_owner_liveness_verifies_matching_start_identity(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("running", "start-abc"))
    metadata = {
        "host": socket.gethostname(),
        "owner_pid": 4321,
        "owner_process_start": "start-abc",
    }

    assert process_identity.owner_liveness(metadata) == "running"


def test_owner_liveness_treats_reused_pid_as_gone(monkeypatch) -> None:
    """A live PID with a different start identity is a different process."""
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("running", "start-new"))
    metadata = {
        "host": socket.gethostname(),
        "owner_pid": 4321,
        "owner_process_start": "start-old",
    }

    assert process_identity.owner_liveness(metadata) == "gone"


def test_owner_liveness_is_unverified_without_recorded_identity(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("running", None))
    metadata = {"host": socket.gethostname(), "owner_pid": 4321}

    assert process_identity.owner_liveness(metadata) == "running-unverified"


def test_owner_liveness_is_unverified_when_identity_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("running", None))
    metadata = {
        "host": socket.gethostname(),
        "owner_pid": 4321,
        "owner_process_start": "start-old",
    }

    assert process_identity.owner_liveness(metadata) == "running-unverified"


def test_owner_liveness_reports_gone_for_a_dead_process(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("gone", None))
    metadata = {"host": socket.gethostname(), "owner_pid": 4321}

    assert process_identity.owner_liveness(metadata) == "gone"


def test_owner_liveness_reports_unknown_when_the_platform_cannot_tell(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "probe_process", lambda pid: ("unknown", None))
    metadata = {"host": socket.gethostname(), "owner_pid": 4321}

    assert process_identity.owner_liveness(metadata) == "unknown"


class FakeKernel32:
    """Stands in for kernel32 so both probe arms run on either platform."""

    def __init__(self, handle=0x1234, times_succeed=True, high=7, low=9):
        self.handle = handle
        self.times_succeed = times_succeed
        self.high = high
        self.low = low
        self.closed = []

    def OpenProcess(self, access, inherit, pid):  # Win32 spelling
        return self.handle

    def GetProcessTimes(self, handle, creation, *rest):  # Win32 spelling
        if not self.times_succeed:
            return 0
        creation._obj.high = self.high
        creation._obj.low = self.low
        return 1

    def CloseHandle(self, handle):  # Win32 spelling
        self.closed.append(handle)
        return 1


def install_fake_windows(monkeypatch, kernel32, last_error=0):
    monkeypatch.setattr(process_identity.ctypes, "WinDLL", lambda *a, **k: kernel32, raising=False)
    # Both names are Windows-only on the real module, so neither may be assumed present.
    monkeypatch.setattr(
        process_identity.ctypes, "get_last_error", lambda: last_error, raising=False
    )


def test_parse_proc_stat_reads_field_22() -> None:
    fields = " ".join(str(number) for number in range(3, 40))
    raw = f"42 (python) {fields}"

    assert process_identity.parse_proc_stat_starttime(raw) == "22"


def test_parse_proc_stat_survives_a_comm_containing_parentheses_and_spaces() -> None:
    fields = " ".join(str(number) for number in range(3, 40))
    raw = f"42 (weird ) name (x)) {fields}"

    assert process_identity.parse_proc_stat_starttime(raw) == "22"


def test_parse_proc_stat_rejects_a_line_with_no_closing_parenthesis() -> None:
    assert process_identity.parse_proc_stat_starttime("42 python 1 2 3") is None


def test_parse_proc_stat_rejects_a_truncated_line() -> None:
    assert process_identity.parse_proc_stat_starttime("42 (python) 1 2 3") is None


def test_linux_probe_reads_a_real_stat_file(tmp_path, monkeypatch) -> None:
    fields = " ".join(str(number) for number in range(3, 40))
    stat_file = tmp_path / "stat"
    stat_file.write_text(f"42 (python) {fields}", encoding="utf-8")
    monkeypatch.setattr(process_identity, "proc_stat_path", lambda pid: stat_file)

    assert process_identity._linux_probe(42) == ("running", "linux-starttime:22")


def test_linux_probe_reports_gone_for_a_missing_stat_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "proc_stat_path", lambda pid: tmp_path / "absent")

    assert process_identity._linux_probe(42) == ("gone", None)


def test_linux_probe_reports_unknown_when_stat_is_unreadable(tmp_path, monkeypatch) -> None:
    unreadable = tmp_path / "as-a-directory"
    unreadable.mkdir()
    monkeypatch.setattr(process_identity, "proc_stat_path", lambda pid: unreadable)

    assert process_identity._linux_probe(42) == ("unknown", None)


def test_linux_probe_runs_without_an_identity_on_a_truncated_line(tmp_path, monkeypatch) -> None:
    stat_file = tmp_path / "stat"
    stat_file.write_text("42 (python) 1 2 3", encoding="utf-8")
    monkeypatch.setattr(process_identity, "proc_stat_path", lambda pid: stat_file)

    assert process_identity._linux_probe(42) == ("running", None)


def test_windows_probe_builds_an_identity_from_the_creation_time(monkeypatch) -> None:
    kernel32 = FakeKernel32(high=7, low=9)
    install_fake_windows(monkeypatch, kernel32)

    state, identity = process_identity._windows_probe(42)

    assert state == "running"
    assert identity == f"windows-createtime:{(7 << 32) | 9}"
    assert kernel32.closed == [0x1234]


def test_windows_probe_closes_the_handle_when_times_fail(monkeypatch) -> None:
    kernel32 = FakeKernel32(times_succeed=False)
    install_fake_windows(monkeypatch, kernel32)

    assert process_identity._windows_probe(42) == ("running", None)
    assert kernel32.closed == [0x1234]


def test_windows_probe_reports_gone_on_invalid_parameter(monkeypatch) -> None:
    install_fake_windows(
        monkeypatch,
        FakeKernel32(handle=0),
        last_error=process_identity.WINDOWS_ERROR_INVALID_PARAMETER,
    )

    assert process_identity._windows_probe(42) == ("gone", None)


def test_windows_probe_treats_access_denied_as_running(monkeypatch) -> None:
    install_fake_windows(
        monkeypatch,
        FakeKernel32(handle=0),
        last_error=process_identity.WINDOWS_ERROR_ACCESS_DENIED,
    )

    assert process_identity._windows_probe(42) == ("running", None)


def test_windows_probe_reports_unknown_on_any_other_error(monkeypatch) -> None:
    install_fake_windows(monkeypatch, FakeKernel32(handle=0), last_error=1234)

    assert process_identity._windows_probe(42) == ("unknown", None)


def test_probe_dispatches_to_linux(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(process_identity, "_linux_probe", lambda pid: ("running", "linux-x"))

    assert process_identity.probe_process(42) == ("running", "linux-x")


def test_probe_dispatches_to_windows(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(process_identity, "_windows_probe", lambda pid: ("running", "win-x"))

    assert process_identity.probe_process(42) == ("running", "win-x")


def test_probe_reports_unknown_on_an_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "sys", SimpleNamespace(platform="darwin"))

    assert process_identity.probe_process(42) == ("unknown", None)


def test_probe_reports_unknown_when_the_operating_system_refuses(monkeypatch) -> None:
    monkeypatch.setattr(process_identity, "sys", SimpleNamespace(platform="linux"))

    def refuse(pid):
        raise OSError("no")

    monkeypatch.setattr(process_identity, "_linux_probe", refuse)

    assert process_identity.probe_process(42) == ("unknown", None)


def test_proc_stat_path_points_at_the_kernel_interface() -> None:
    assert process_identity.proc_stat_path(42).as_posix() == "/proc/42/stat"


def test_owner_liveness_ignores_the_ephemeral_creator_pid() -> None:
    """The acquiring CLI process is dead on every healthy lock.

    Reporting on it would mark live locks abandoned, so liveness is claimed
    only for a process the caller explicitly nominates.
    """
    metadata = {"host": socket.gethostname(), "creator_pid": os.getpid()}

    assert process_identity.owner_liveness(metadata) == "unknown"
