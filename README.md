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

## Development

```bash
python -m pytest -q
ruff check scripts tests
ruff format --check scripts tests
```

The runtime uses only the Python standard library.

## License

MIT
