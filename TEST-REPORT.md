skills-project-lock test report - 2026-08-05T22:05:00Z
========================================================

Git:      main @ fbc6588 (pre-commit working tree)
Status:   PASS (3 machine-local failures, see below)
Tests:    143 passed, 3 failed, 0 skipped
Coverage: 656/658 statements, 186/188 branches (99.5%) on scripts/project_lock
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
