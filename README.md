# skills-project-lock

Cooperative project and Git-worktree locks for autonomous coding agents.

The repository contains an Agent Skill plus a dependency-free Python CLI. Agents check and acquire a lock before writing outside or inside their starting directory. Lock metadata tells contenders who is working, why, when they expect to finish, and whether waiting or a separate worktree is preferred.

## Quick start

```bash
python scripts/project-lock.py check .
python scripts/project-lock.py acquire . --reason "main session" --duration 2h --strategy worktree
python scripts/project-lock.py list
python scripts/project-lock.py watch
python scripts/project-lock.py release . --lock-id <id>
```

A lock is the atomic directory `<root>/.agent-lock/`, with versioned metadata in `owner.json`. Git's local exclude file hides the marker from `git status`. Per-user SQLite serialization makes acquire, renew, and release generation-safe, and a per-user registry powers the watcher.

Elapsed estimates become **overdue**, never automatically free. Clear an abandoned lock only after verifying its owner is gone:

```bash
python scripts/project-lock.py release /path/to/project --force
```

## Decisions

- Wait for short work, same-branch changes, maintenance, merges, rebases, and shared mutable state.
- Use a separate Git worktree for independent long-running edits.
- Lock the new worktree before editing it.
- Use a real transactional lease service for cross-host or high-impact external operations.

## Enforcement hook

`hooks/pre_tool_use.py` is a mechanical `PreToolUse` hook: wire it in the user's own `settings.json` (portable form, no machine paths):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.claude/skills/project-lock/hooks/pre_tool_use.py\""
          }
        ]
      }
    ]
  }
}
```

Windows settings use `%USERPROFILE%` semantics: expand via `$env:USERPROFILE` or write an absolute per-machine path. Settings are per-machine config, not a shipped file, so this snippet is a template to adapt, not something the installer writes for you.

The hook denies `Edit`/`Write`/`NotebookEdit` calls whose target is governed by another session's lock, and denies unlocked writes with the exact acquire command (including `--session` when the payload carries one). For `Bash`, the command is split into segments on `;`, `&`, `|`, and newlines, and each segment is classified independently; the call is denied only when the session's cwd sits in a foreign jurisdiction and the command is not read-only, or when the command spells out an absolute path under a registered foreign lock. Read-only recognition covers a fixed allowlist (`ls`, `cat`, `head`, `tail`, `grep`, `rg`, `pwd`, `echo`, `which`, `type`), `find` without a mutating flag (`-delete`, `-exec`, `-execdir`, `-ok`, `-okdir`, `-fprint*`, `-fls`), and `git status`/`log`/`diff`/`show`/`ls-files`/`rev-parse`. `git branch` and `git remote` are deliberately treated as non-read-only across the board: their mutating forms are indistinguishable from their read forms to a word-level classifier, so the safe default is to require a lock. Any segment containing `>`, `$(`, a backtick, `<(`, or `--output` is treated as a write regardless of the leading command. Enforcement modes: `PROJECT_LOCK_ENFORCE=deny` (default), `warn` (logs but allows), `off`. The hook fails open on its own errors so a bug in the check never bricks the harness.

This is bounded coverage, not a sandbox: relative or quoted paths that reach a foreign jurisdiction from outside it are not caught. Hold the lock for every jurisdiction a command spans, especially recursive operations (`rm -rf`, `git clean`, formatter sweeps, `git add -A`).

## Development

```bash
python -m pytest -q
ruff check scripts tests
ruff format --check scripts tests
```

The runtime uses only the Python standard library.

## License

MIT
