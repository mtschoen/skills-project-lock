skills-project-lock test report - 2026-08-05T22:05:00Z
========================================================

Git:      main @ 51a0fe8 (pre-commit working tree)
Status:   PASS (3 machine-local failures, see below)
Tests:    145 passed, 3 failed, 0 skipped
Coverage: 661/663 statements, 190/192 branches (99.5%) on scripts/project_lock
Lint:     ruff check: clean
          ruff format --check: clean
          markdownlint-cli2: 0 findings on tracked files
          agentskills validate (skills-ref 0.1.1): valid
          aislop ci: 0 errors, 0 warnings

Commands
--------

```bash
python -m pytest -q
ruff check scripts tests hooks
ruff format --check scripts tests hooks
npx --yes markdownlint-cli2
uvx --from skills-ref==0.1.1 agentskills validate
```

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

Machine-local failures
----------------------

The 3 failures and the 2 uncovered lines are artifacts of the authoring
machine, not of the code. CI (ubuntu-latest) is unaffected and enforces the
100% gate.

- `test_nearest_worktree_root`, and both
  `test_scratch_path_with_no_git_ancestor_under_temp_allows_edit` variants:
  an empty stray `~/.git` on that Windows host makes `has_git_ancestor()`
  true for the whole home tree, including `%TEMP%`, which disables the
  scratch carve-out. This is the exact hazard documented under "Known
  limitations of the scratch carve-out" in SKILL.md. All three were verified
  to fail identically on the unmodified parent commit.
- `core.py:90` is the "walked to the filesystem root without finding `.git`"
  return, unreachable on that host for the same reason.
- `core.py:132` is the `os.name != "nt"` branch, unreachable on Windows.

Smoke (SMOKE.md, live installed skill)
--------------------------------------

- Force-clear without `--reason` refused; exit 3.
- `check` on a lock with no nominated process: `owner pid: - (unknown)`.
- `acquire --owner-pid <live Win32 pid>` then `check`: `running`.
- Same lock after killing that process: `gone`, while `advice` still reads
  "wait, then check again" rather than declaring the lock free.
- Git-admin ownership, all seven cases against real `git worktree` fixtures:
  idle repo allows `config`, `info/exclude` and `hooks/pre-commit`; holding the
  main checkout allows `config`; another session holding main denies; holding
  only a linked worktree denies; that worktree's own content still allows.

Markdownlint reports findings under `.superpowers/sdd/`, which is git-ignored
scaffolding (`.superpowers/sdd/.gitignore` contains `*`) and never present in
a CI checkout. Tracked markdown is clean.
