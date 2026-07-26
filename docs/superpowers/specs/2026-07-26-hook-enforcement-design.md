# Hook enforcement for project-lock

Date: 2026-07-26. Status: approved direction, spec for plan handoff.

## Problem

The cooperative skill relies on agents choosing to run `check` before writing.
In practice agents skip it, and nothing mechanical prevents two sessions from
racing on the same file or overwriting each other's work. The daemon-v1 design
attempted authoritative enforcement and collapsed under its own scope (its
design record is preserved on the `docs/project-lock-recovery` branch). This
spec adds mechanical enforcement at the harness-hook layer instead: no daemon,
no OS-level machinery, standard library only.

A second unresolved question falls out of the same design: a Git worktree that
lives as a subfolder of another checkout (`<repo>/.worktrees/feature`,
`.claude/worktrees/*`) versus a sibling folder. Today the CLI resolves each
path to its own worktree root and the two locks are mutually invisible.

## Goal

1. A `PreToolUse` hook that DENIES file-tool writes governed by another
   session's live lock, and denies writes with no lock held with a message
   containing the exact acquire command (deny-with-recipe).
2. Bounded Bash coverage: cwd-jurisdiction check plus absolute-path-token
   scan; no shell parsing ambitions beyond that.
3. Nested-worktree jurisdiction semantics that make the worktree-as-subfolder
   case correct by construction.
4. Keep the existing cooperative CLI, prose guidance, and lock protocol
   backward-readable. Claude Code first; other harnesses later.

## Non-goals

- No daemon, no authoritative broker, no OS sandbox/ACLs.
- No claim of TOCTOU protection or protection from adversarial processes.
- No attempt to classify arbitrary shell commands as mutating; the residue
  (Bash writes from an outside cwd via relative or quoted paths) stays covered
  by prose nudges and is documented as the known gap.

## Components

### 1. Jurisdiction resolution (core.py addition)

`governing_lock(path)` walks ancestors of the target path looking for
`.agent-lock/` markers, git-free:

- Walk from the target file's directory upward.
- At each level, a `.agent-lock/` directory with readable `owner.json` is a
  candidate governing lock.
- A `.git` entry (directory or gitfile) marks a worktree/repo root: the walk
  may inspect that level's `.agent-lock`, then STOPS. Ancestor locks beyond a
  `.git` boundary never govern paths below it.

Consequences: a nested linked worktree (its root has a `.git` file) is its own
jurisdiction; the containing checkout's lock never reaches inside it, and vice
versa. Sibling worktrees are unaffected. Non-git directories keep today's
behavior (lock at the literal root). This runs with a handful of stats and no
subprocess - it is the hot path for every Edit/Write.

`related_locks(root)` (for `check`/`list`/`acquire` visibility, not for the
hook hot path) reports ancestor locks beyond the boundary and descendant locks
found via the per-user registry (path-prefix test over registered roots).
`check` and `acquire` print these as informational lines; `list`/`watch` group
entries that share a repository.

### 2. Session identity (protocol change, backward-readable)

`owner.json` gains additive fields: `session_id`, `harness`
(e.g. `claude-code`), and `hostname`. Old CLI versions ignore unknown keys;
new code treats their absence as "legacy lock" (enforced against everyone
except via lock id). The hook reads the harness-provided session id from the
hook input JSON and matches it against the lock's `session_id` so a session
never blocks itself. `acquire` records the session id via an explicit
`--session-id` flag. The harness does not export the session id to shell
commands, but the denying hook knows it from its stdin payload, so the
deny-with-recipe message embeds `--session-id <id>` in the suggested command;
manual human locks simply omit the flag and keep working.

### 3. The hook (hooks/pre_tool_use.py)

Single stdlib script, dispatched on `PreToolUse` for
`Edit|Write|NotebookEdit|Bash`. Reads hook JSON from stdin.

File tools:
- Resolve `tool_input.file_path` via `governing_lock`.
- Foreign live lock (session id mismatch) -> deny (exit 2 with reason on
  stderr): owner, reason, expected-until, and the wait/worktree advice line.
  Overdue foreign locks still deny (overdue is a wrong estimate, not a free
  lock) with the overdue status noted.
- Own lock -> allow.
- No lock -> deny with recipe: the exact
  `python <script> acquire <root> --reason ... --duration ...` command to run.
  Mode switch `PROJECT_LOCK_ENFORCE=deny|warn|off` (default `deny`; `warn`
  allows but injects a warning) for rollout and escape hatch.

Bash tool:
- Resolve the session cwd (from hook input) via `governing_lock`. If governed
  by a foreign lock and the command's first word is not in a read-only
  allowlist -> deny with the same owner/advice message. The allowlist is a
  frozen set in the hook source (initial contents: `ls`, `cat`, `head`,
  `tail`, `grep`, `rg`, `find`, `pwd`, `echo`, `which`, `type`, plus `git`
  limited to the read subcommands `status`, `log`, `diff`, `show`, `branch`,
  `remote`, `ls-files`, `rev-parse`); extending it is a code change, not
  configuration.
- Scan the command string for absolute-path tokens (regex for
  `[A-Za-z]:[\\/][^\s'\"]+` and `/[^\s'\"]+`); for each token, check against
  the registry's known lock roots only (cheap prefix test, no filesystem
  walk). A token under a foreign lock root -> deny naming that root.
- Everything else -> allow. No mutating-verb table.
- No-lock-held is NOT enforced for Bash (cwd may legitimately be outside any
  project); only foreign-lock collisions deny.

Failure policy: any unhandled hook exception -> allow (fail-open) after
writing a one-line diagnostic to stderr. A cooperative tool must not brick the
harness on its own bugs. Documented as a limitation.

Latency budget: under 50ms added per intercepted call on Windows (measured in
tests); no subprocess, no git, single registry directory listing memoized per
invocation.

### 4. Packaging and installation

- `hooks/` ships via `.skillpack` (same mechanism as progress-beacon).
- SKILL.md gains an "Enforcement" section: what the hook does, the
  deny-with-recipe flow, the Bash residue, and installation wiring
  (`PreToolUse` matcher in `.claude/settings.json`, per-project or
  user-global). Prose nudges ("think before you Bash") stay.
- Other harnesses (Codex, Antigravity, opencode) get a documented adapter
  point: the hook script takes `--input-format claude` today; adding formats
  later does not change core.

### 5. Worktree-as-subfolder documentation

SKILL.md's "Protocol and limitations" section documents the jurisdiction rule
explicitly: nearest enclosing worktree governs; ancestor locks stop at `.git`
boundaries; `check` reports related ancestor/descendant locks so subtree-wide
operations (recursive delete, `git clean`, formatter sweeps, `git add -A`)
can be manually cleared first. Rule of thumb shipped in prose: hold the locks
of every jurisdiction your command spans.

## Testing

- Real git fixtures: main checkout + nested worktree (`.worktrees/feature`)
  + sibling worktree, plus a plain non-git directory. Assert jurisdiction
  resolution, both invisibility directions fixed, boundary stop, related-locks
  reporting.
- Hook tests drive `pre_tool_use.py` as a subprocess with Claude-shaped JSON
  on stdin: foreign-deny, own-allow, no-lock recipe, warn/off modes, Bash cwd
  deny, Bash read-only allow, path-token deny, fail-open on malformed input.
- Latency test asserts the budget on the file-tool path.
- Protocol compatibility: old-format `owner.json` (no session fields) still
  read by new code; new-format file readable by the released CLI.
- Existing suite stays green; ruff + `agentskills validate` + aislop gates per
  repo AGENTS.md; TEST-REPORT.md refreshed; SMOKE.md followed before release.

## Rollout

1. Land in this repo, install on chonkers (skills install + settings hook
   wiring), dogfood in real sessions.
2. Fleet-wide settings wiring (llamabox, steamdeck) after a few days of
   chonkers soak.
3. Revisit harness adapters (Codex/Antigravity/opencode) once the Claude Code
   loop proves out.
