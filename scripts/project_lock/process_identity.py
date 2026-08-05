"""Owner-process liveness, with PID-reuse protection where the platform allows.

A recorded PID alone cannot answer "is the owner still alive": PIDs are reused,
so a live PID may belong to an unrelated process. Recording a *start identity*
alongside the PID at acquire time closes that gap.

The two answers are deliberately asymmetric. `gone` is strong evidence a
session is abandoned. `running` only means something is there. Nothing here
ever clears a lock on its own; it supplies the evidence a human or supervising
agent needs before choosing to force-clear.

`ctypes.wintypes` is deliberately not imported: it raises on non-Windows, which
would make the Windows probe untestable elsewhere. The one FILETIME field pair
this module needs is declared locally instead, leaving `ctypes.WinDLL` as the
only Windows-only symbol.
"""

from __future__ import annotations

import ctypes
import socket
import sys
from pathlib import Path
from typing import Any

# /proc/<pid>/stat field 22 is starttime. Fields are 1-indexed and the first
# two (pid, comm) precede the split point, so it is index 19 after the comm.
PROC_STAT_STARTTIME_INDEX = 19
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_ERROR_ACCESS_DENIED = 5
WINDOWS_ERROR_INVALID_PARAMETER = 87


class FileTime(ctypes.Structure):
    """Windows FILETIME: a 64-bit tick count split across two 32-bit halves."""

    _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))


def proc_stat_path(pid: int) -> Path:
    return Path(f"/proc/{pid}/stat")


def parse_proc_stat_starttime(raw: str) -> str | None:
    """Extract field 22 (starttime) from a `/proc/<pid>/stat` line.

    `comm` is parenthesized and may itself contain spaces and closing
    parentheses, so the only reliable split point is the *final* `)`.
    """
    _, separator, tail = raw.rpartition(")")
    if not separator:
        return None
    fields = tail.split()
    if len(fields) <= PROC_STAT_STARTTIME_INDEX:
        return None
    return fields[PROC_STAT_STARTTIME_INDEX]


def _linux_probe(pid: int) -> tuple[str, str | None]:
    try:
        raw = proc_stat_path(pid).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "gone", None
    except OSError:
        return "unknown", None
    starttime = parse_proc_stat_starttime(raw)
    if starttime is None:
        return "running", None
    return "running", f"linux-starttime:{starttime}"


def _windows_probe(pid: int) -> tuple[str, str | None]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == WINDOWS_ERROR_INVALID_PARAMETER:
            return "gone", None
        if error == WINDOWS_ERROR_ACCESS_DENIED:
            # The process exists; this account may not query it.
            return "running", None
        return "unknown", None
    try:
        creation = FileTime()
        discard = (FileTime(), FileTime(), FileTime())
        succeeded = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(discard[0]),
            ctypes.byref(discard[1]),
            ctypes.byref(discard[2]),
        )
        if not succeeded:
            return "running", None
        return "running", f"windows-createtime:{(creation.high << 32) | creation.low}"
    finally:
        kernel32.CloseHandle(handle)


def probe_process(pid: int) -> tuple[str, str | None]:
    """Return `(state, start_identity)` for `pid`.

    State is `running`, `gone`, or `unknown`. `unknown` means this platform
    cannot answer, never that the process is absent. The identity is `None`
    whenever it could not be read, which callers must not confuse with a
    mismatch.
    """
    try:
        if sys.platform.startswith("linux"):
            return _linux_probe(pid)
        if sys.platform == "win32":
            return _windows_probe(pid)
    except OSError:
        return "unknown", None
    return "unknown", None


def process_start_identity(pid: int) -> str | None:
    """Opaque start identity for `pid`, or `None` where unavailable."""
    return probe_process(pid)[1]


def owner_liveness(metadata: dict[str, Any]) -> str:
    """Classify the recorded owner process of a lock.

    One of `running`, `running-unverified`, `gone`, or `unknown`. Only `gone`
    is evidence of abandonment; `running-unverified` means a process holds the
    PID but its identity could not be confirmed against the recorded one.

    Reads `owner_pid`, which a caller supplies explicitly, and never
    `creator_pid`. The acquiring CLI process exits as soon as it writes the
    marker, so its pid is dead for every healthy lock and would report `gone`
    on all of them. Absent an explicitly nominated process, the honest answer
    is `unknown`.
    """
    if metadata.get("host") != socket.gethostname():
        return "unknown"
    pid = metadata.get("owner_pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "unknown"
    state, identity = probe_process(pid)
    if state != "running":
        return state
    recorded = metadata.get("owner_process_start")
    if not recorded or not identity:
        return "running-unverified"
    return "running" if identity == recorded else "gone"
