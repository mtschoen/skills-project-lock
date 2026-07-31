skills-project-lock test report - 2026-07-26T13:30:22Z
=====================================================

Status:   PASS
Mode:     final-review fix pass (post-review findings on top of Task 7's gate
          refresh); see "Final-review fix pass" section below
Tests:    67 total (4 new: tilde-path, $VAR-path, read-only-skips-token-scan
          x2)
Git:      branch feat/hook-enforcement, HEAD 16d5579 plus this pass's changes
Coverage: 467/467 statements (100%); 122/122 branches (100%)
          0 lines uncovered
          0 exclusion annotations
Lint:     ruff check scripts tests hooks: 0 findings
          ruff format --check scripts tests hooks: 9 files already formatted
          markdownlint-cli2: 0 findings in shipped docs (README.md, SKILL.md,
            AGENTS.md, SMOKE.md, TEST-REPORT.md); findings exist only in
            scaffolding not part of the shipped skill (see Concerns)
          aislop 0.14.0: score 100 (2 findings fixed, 2 findings suppressed
            via aislop-ignore directives with recorded reasons; see Concerns)
          Agent Skills validator 0.1.1 (`uvx --from skills-ref==0.1.1
            agentskills validate ../project-lock`): valid
          0 per-case suppressions
          2 documented exceptions (aislop-ignore directives, see Concerns)

Commands run
------------

```bash
python -m pytest -q --basetemp=C:/Users/Public/pytest-pl-tmp
ruff check scripts tests hooks
ruff format --check scripts tests hooks
uvx --from skills-ref==0.1.1 agentskills validate ../project-lock
aislop ci .
npx --yes markdownlint-cli2
```

`--basetemp` was pointed outside the default pytest tmp root (which lives
under the user's home directory) because a stray `.git` directory at the user's home
breaks `test_core.py::test_nearest_worktree_root` when pytest's tmp factory
resolves under home; this is a machine artifact of this workstation, not a
project or CI defect (CI runs on ubuntu-latest with no such artifact). The
custom basetemp directory was deleted after the run.

pytest: 67 passed, 100% line and branch coverage on `scripts/project_lock`
(`__init__.py` 2/2, `cli.py` 159/159 + 24/24 branches, `core.py` 306/306 +
98/98 branches).

Docs drift check
-----------------

Re-checked every behavioral claim in README.md, SKILL.md, and AGENTS.md
against `hooks/pre_tool_use.py`:

- `READ_ONLY_COMMANDS = {ls, cat, head, tail, grep, rg, pwd, echo, which,
  type}` - matches docs; no `find` in this set (docs correctly describe
  `find` as allowed only when it carries no mutating flag, handled
  separately).
- `GIT_READ_SUBCOMMANDS = {status, log, diff, show, ls-files, rev-parse}` -
  matches docs; `git branch`/`git remote` deliberately excluded and
  documented as always-mutating.
- `FIND_MUTATING_FLAGS = {-delete, -exec, -execdir, -ok, -okdir, -fprint,
  -fprint0, -fprintf, -fls}` - matches the "find without a mutating flag"
  description.
- Deny-on-sight segment tokens `>`, `$(`, backtick, `<(`, `--output` - match
  docs verbatim.
- `SEGMENT_SPLIT_PATTERN` splits on `;`, `&`, `|`, and newlines - matches
  docs.
- Enforcement modes `deny` (default) / `warn` / `off`, fail-open on internal
  errors - matches docs in README.md, SKILL.md, and the hook's own module
  docstring.
- Jurisdiction rule (nearest enclosing Git worktree; a `.git` boundary stops
  an ancestor's lock from reaching a nested worktree) matches
  `governing_lock`'s walk in `scripts/project_lock/core.py`.

No drift found; no doc edits were needed.

Smoke test
----------

Followed `SMOKE.md` in a disposable git repository
(`C:\Users\Public\pl-smoke-bar`, deleted afterward):

- Floor: `python scripts/project-lock.py --help` printed the subcommand list.
- Bar: `acquire . --reason "smoke bar test" --duration 5m --json` returned a
  lock id; `check .` exited 3 and printed the owner/reason/branch/advice;
  `release . --lock-id <id>` printed `RELEASED .`; `check .` then printed
  `FREE` and exited 0.

Real-harness smoke (brief Step 4, live Claude Code session): built a
throwaway project at `C:\Users\Public\pl-smoke` (git init, one commit), wired
its `.claude/settings.json` `PreToolUse` hook to the absolute path of this
checkout's `hooks/pre_tool_use.py` (machine path acceptable in a throwaway,
never in shipped files), then:

1. Acquired a lock on the throwaway root with a fake session id:
   `acquire . --reason "smoke live-harness test" --duration 15m --session
   fake-session-id --json`.
2. Ran, from that directory:
   `claude -p "Append the line smoke-test to README.md in this directory
   using the Edit or Write tool" --allowedTools "Edit,Write,Read"`.
   Result: **DENIED**. The session's own transcript reported the hook
   blocked the Edit ("Your `.claude` project-lock hook blocked the Edit ...
   locked by mtsch@Chonkers, reason 'smoke live-harness test', lock id
   e7456f08 ..."), and `README.md` was verified unchanged (`# smoke`) on
   disk after the run.
3. Released the fake-session lock (`release . --lock-id
   e7456f08-19b0-48e3-8eed-1a3fa7715eeb`), confirmed `check` printed `FREE`.
4. Re-ran the same `claude -p` prompt (now with `Bash` also allowed) against
   the unlocked project. Result: **ALLOWED after correct self-acquire**. The
   hook denied the first unlocked-write attempt with the acquire recipe; the
   session followed it, acquired the lock under its own session id, edited
   the file, and released. `README.md` was verified changed to `# smoke` /
   `smoke-test` on disk afterward, and `check` confirmed the lock was
   released (`FREE`).

Observation (not a defect): the acquire recipe the hook prints is built from
`Path(__file__).resolve().parents[1] / "scripts" / "project-lock.py"`, i.e.
whichever copy of the skill is actually running. Because this smoke test
deliberately wired the throwaway project's hook to this dev checkout's
`hooks/pre_tool_use.py` (per the brief, since a normal install would point at
`~/.claude/skills/project-lock/hooks/pre_tool_use.py`), and this dev checkout
was itself locked for unrelated reasons (the outer session's own
`hook-enforcement impl` lock, untouched by this test), the printed recipe
pointed at a script path that the Bash absolute-path-token check treats as
foreign-jurisdiction. The live session noticed and used its installed skill
copy (`~/.claude/skills/project-lock/scripts/project-lock.py`) instead,
completing the acquire/edit/release cycle successfully. This is an artifact
of using the same repo as both "tool under test" and "hook source" in this
smoke rig, not a production concern for a normal install.

Cleanup performed: released both smoke locks, deleted
`C:\Users\Public\pl-smoke-bar` and `C:\Users\Public\pl-smoke`, and confirmed
`python scripts/project-lock.py list` shows only the pre-existing,
legitimate lock on this checkout itself (unrelated to the smoke test).

Concerns
--------

`aislop ci .` originally scored 77 (gate requires 100), with four findings on
files touched by this branch's hook-enforcement work. Controller adjudication
resolved all four: two fixed legitimately, two suppressed inline with a
recorded reason. `aislop ci . --human` now reports `Suppressed 2 finding(s)
via aislop-ignore directives` and `100 / 100  Healthy  no issues`.

Fixed (2, real code changes, no suppression):

1. `hooks/pre_tool_use.py` `check_file_tool` - `ai-slop/python-chained-dict-get`:
   `payload.get("tool_input", {}).get(FILE_TOOL_PATH_KEYS[...])` rewritten as
   `tool_input = payload.get("tool_input") or {}` followed by
   `tool_input.get(...)`, matching the pattern `cli.py` already uses.
   Behavior is identical; the two-step form no longer reads as a chained
   fallback that hides a missing-`tool_input` case.
2. `hooks/pre_tool_use.py` `check_bash` - same rewrite for
   `payload.get("tool_input", {}).get("command", "")`.

Suppressed (2, `aislop-ignore` directives with reasons):

1. `hooks/pre_tool_use.py`, line above `from project_lock.core import (...)`:
   `# aislop-ignore-next-line ai-slop/hallucinated-import -- sys.path-resolved
   sibling; stdlib-only`. `project_lock` is a sibling package reached via the
   runtime `sys.path.insert(...)` two lines above, not a manifest-declared
   install; this is the intended mechanism for a hook script that must run
   standalone without packaging the skill as an installable dependency.
   Assessed as a false positive in Task 6; now formally suppressed instead of
   left as an unexplained gate failure.
2. `scripts/project_lock/core.py`, below the module docstring:
   `# aislop-ignore-file complexity/file-too-large -- cohesive protocol core;
   split tracked separately`. `core.py` crossed aislop's 400-line soft limit
   during this branch's `governing_lock`/`related_locks` work (Tasks 2-3); it
   is still under this project's own AGENTS.md/user guideline of ~500 lines.
   Splitting a single cohesive module (lock protocol: paths, metadata,
   mutation guard, CLI-facing operations) into multiple files is tracked as a
   future refactor, not done in this task.

Both `aislop-ignore` reason strings were kept within ruff's 100-column line
limit (`E501`) so the suppression comments themselves do not introduce new
lint findings.

markdownlint-cli2 (run with no path filter, matching CI's invocation) reports
findings only in:

- `.superpowers/sdd/*` - gitignored local scratch (task briefs/reports from
  subagent-driven-development), never committed, never seen by CI.
- `docs/superpowers/plans/2026-07-26-hook-enforcement.md` - the tracked WIP
  implementation plan for this branch, explicitly out of scope for this
  task's commit (deleted at branch-finish per
  `finishing-a-development-branch`, not before).

Zero findings in any shipped file. This is expected transient branch state,
not a defect to fix here.

Final-review fix pass
----------------------

Applied whole-branch review findings on top of the Task 7 gate refresh above
(commit on top of 16d5579):

1. Closed the `~`/`$HOME` false-allow in the Bash path-token scan
   (`hooks/pre_tool_use.py`): `PATH_TOKEN_PATTERN` gained a `~`-prefixed arm
   and a `$VAR`-prefixed arm (previously the regex only anchored a match at
   an embedded `/`, dropping the `~` or `$HOME` prefix entirely and letting
   `echo x > ~/locked-repo/f.txt` slip through unmatched against the lock
   root). Each matched token is now expanded (`os.path.expandvars` then
   `os.path.expanduser`) before `normcase`/`normpath` comparison.
2. Gated the registry token-scan loop in `check_bash` behind
   `not command_is_read_only(command)`, matching the cwd-jurisdiction branch
   immediately above it. Read-only commands (`cat`, `git log`, and friends)
   that merely reference a path under a foreign lock are no longer denied;
   redirection (`>`) or other mutation still is.
3. Added 4 tests to `tests/test_hook.py`: tilde-path denial, `$HOME`-path
   denial, read-only `cat` on a foreign absolute path (allow), and read-only
   `git log` on a foreign absolute path (allow). Existing token-scan deny
   tests (`echo x > "<path>"`) all involve redirection, so they remain
   non-read-only and stay green under the new gate.
4. Hardened `tests/conftest.py`'s `run_git_command`: added
   `-c commit.gpgsign=false -c core.hooksPath=` (isolates the fixture from
   host git config that could hang or misbehave, e.g. a gpgsign host) and
   `timeout=30` to the `subprocess.run` call.
5. Documented the deliberate unlocked-Bash-allowed asymmetry and the
   quoted-path-with-spaces gap in both `SKILL.md` and `README.md`, plus a
   README-only rollout recommendation (`PROJECT_LOCK_ENFORCE=warn` for the
   first day of a global install).

Commands run for this pass (all from the repo root):

```bash
python -m pytest -q --basetemp=C:/Users/Public/pytest-pl-tmp
ruff check scripts tests hooks
ruff format --check scripts tests hooks
aislop ci .
npx --yes markdownlint-cli2 "SKILL.md" "README.md"
```

Outcomes: pytest 67 passed, 100% line and branch coverage (unchanged
statement/branch totals: `scripts/project_lock` 467/467 + 122/122); ruff
check and format both clean; `aislop ci .` 100/100 (same 2 pre-existing
suppressed findings as the Task 7 baseline, 0 new); markdownlint-cli2 clean
on `SKILL.md`/`README.md` (repo-wide run still shows only the same
pre-existing scratch/plan findings noted above, untouched by this pass). The
`--basetemp` directory was deleted after the run.
