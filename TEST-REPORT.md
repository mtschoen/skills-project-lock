skills-project-lock test report - 2026-08-06T00:25:00Z
========================================================

Git:      main @ 7625090 (pre-commit working tree)
Status:   PASS (Windows local and ubuntu-latest CI)
Tests:    148 passed, 0 failed, 0 skipped
Coverage: 662/663 statements, 191/192 branches (99.8%) on scripts/project_lock
Lint:     ruff check: clean
          ruff format --check: clean
          markdownlint-cli2: 0 findings on tracked files
          agentskills validate (skills-ref 0.1.1): valid
          aislop ci: exit 0

Commands
--------

```bash
python -m pytest -q
ruff check scripts tests hooks
ruff format --check scripts tests hooks
npx --yes markdownlint-cli2
uvx --from skills-ref==0.1.1 agentskills validate
```

Coverage note
-------------

The single uncovered line is `core.py:132`, the `os.name != "nt"` arm of
`_temp_roots()` that adds the POSIX temp mounts. It is unreachable on Windows
and covered on Linux, where CI (ubuntu-latest) enforces the 100% gate.

An earlier revision of this report recorded three failures and a second
uncovered line as permanent machine-local artifacts. They were caused by an
empty stray `~/.git` on the authoring host, which made `has_git_ancestor()`
true for the whole home tree and so disabled the scratch carve-out under
`%TEMP%`. That directory was deleted on 2026-08-05 and all three now pass.

Platform parity
---------------

A green Windows suite is not evidence of a green CI. The owner-liveness tests
faked `ctypes.get_last_error` with monkeypatch's default `raising=True`; that
name exists only on Windows builds of `ctypes`, so all five died on
ubuntu-latest and dropped coverage to 97, failing the 100% gate. CI was red
from the liveness commit onward while this report said PASS, because every
local run had the attribute. Anything patched onto a platform-conditional
standard-library name needs `raising=False`, and a report generated on one
platform should be read against the CI run for the same commit.

Performance (chromium: 500,533 tracked files, 1.5 GB .git)
----------------------------------------------------------

Measured against `~/aislop-stress/chromium` and a tiny repo. Costs are
identical in both, so repository size does not enter any hook path: the git
commands used (`rev-parse`, `worktree list`) read small metadata files and
never touch the index or working tree.

| Path | Checks only | Whole hook process |
| --- | --- | --- |
| Ordinary content edit (the 99% case) | 1.3 ms | ~160 ms |
| `.git` administration edit (rare) | 32 ms | ~190 ms |

`is_git_admin_path` costs 0.00 ms on the content path: the `.git`-component
pre-filter returns before any subprocess. The ~160 ms is Python interpreter
startup (111 ms for a bare `python -c pass` on this host) plus imports, and is
unchanged by this feature.

Asking `rev-parse` for `--git-dir` and `--git-common-dir` in one invocation
costs the same as asking for either alone, which took the admin path from
three subprocesses to two (43 ms to 32 ms).

Smoke (SMOKE.md, live installed skill)
--------------------------------------

- Force-clear without `--reason` refused; exit 3.
- `check` on a lock with no nominated process: `owner pid: - (unknown)`.
- `acquire --owner-pid <live Win32 pid>` then `check`: `running`.
- Same lock after killing that process: `gone`, while `advice` still reads
  "wait, then check again" rather than declaring the lock free.
- `acquire --owner --owner-pid` after the `Claimant` grouping: all three
  claimant fields reach `owner.json` unchanged, and `creator_pid` stays
  distinct from `owner_pid`.
- Liveness against a real Win32 pid with no handle retained by the launcher:
  `running` while alive, `gone` after `kill -9`. Caveat observed while
  smoke-testing: if the launcher still holds an open handle to the exited
  process (a PowerShell `Start-Process -PassThru` object does), Windows keeps
  the pid openable and liveness keeps reporting `running`. That errs in the
  safe direction, since the pid also cannot be reused while the handle lives,
  so the lock is simply treated as still held.
- Git-admin ownership, all seven cases against real `git worktree` fixtures:
  idle repo allows `config`, `info/exclude` and `hooks/pre-commit`; holding the
  main checkout allows `config`; another session holding main denies; holding
  only a linked worktree denies; that worktree's own content still allows.

Markdownlint reports findings under `.superpowers/sdd/`, which is git-ignored
scaffolding (`.superpowers/sdd/.gitignore` contains `*`) and never present in
a CI checkout. Tracked markdown is clean.
