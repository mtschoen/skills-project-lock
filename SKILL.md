---
name: project-lock
description: Use before making changes in any project or repository, especially when a session reaches outside its starting working directory. Checks for a cooperative project lock, acquires one before writes, chooses wait versus worktree when another agent holds it, renews estimates during long work, and releases locks when leaving. Also use when the user asks which projects are locked, whether a repo is busy, or to clear an abandoned agent lock.
---

# project-lock

Coordinate agent writes with an advisory lock at the project or Git worktree root.

## Non-negotiable rule

Before the first write, mutating command, branch operation, dependency install, formatter, or generated-file update in **every project you touch**:

1. Resolve this skill's `scripts/project-lock.py` path.
2. Run `python <script> check <target-path>`.
3. If free, acquire before changing anything.
4. If locked, follow the reported advice. Do not edit first and negotiate later.

Reads and non-mutating inspection do not require a lock.

## Acquire

Give other agents enough context to decide whether to wait or use a worktree:

```bash
python <script> acquire <project-path> \
  --reason "fix XYZ and run its focused tests" \
  --duration 30s \
  --strategy wait
```

Use:

- `--strategy wait` for a short change or shared-state operation.
- `--strategy worktree` when this is the session's main project or lengthy work.
- `--strategy auto` when duration alone should decide: up to five minutes recommends waiting; longer recommends a worktree.

Capture the returned `lock id`; it proves ownership for renew and release. A Git path resolves to its current worktree root. Separate Git worktrees therefore have separate locks.

## When another lock exists

`check` exits `3` and prints owner, reason, expected completion, branch, and advice.

- **wait**: use `python <script> wait <path> --timeout 5m`, or do unrelated read-only work and check again.
- **worktree**: create a separate branch and Git worktree. Acquire a lock inside that new worktree before writing there.
- **overdue**: overdue means the estimate was wrong, not that the lock is free. Contact the owner or ask the user. Force-clear only after verifying the session is abandoned.

Never reuse Git's internal `.lock` files. `git worktree lock` prevents pruning; it does not coordinate agent edits.

## Keep and release

Renew before the estimate expires:

```bash
python <script> renew <path> --lock-id <id> --duration 20m
```

Release immediately after the last mutation, including on failure or when abandoning the task:

```bash
python <script> release <path> --lock-id <id>
```

For sessions touching multiple projects, track each path and lock id separately. Release all of them before wrapping up.

## Watch and override

List once or run the refreshing terminal dashboard:

```bash
python <script> list
python <script> watch
```

After independently verifying a lock is abandoned, the user or supervising agent may clear it:

```bash
python <script> release <path> --force
```

Never force-clear merely because `expected_until` passed.

## Protocol and limitations

Acquisition atomically creates `<root>/.agent-lock/`; metadata lives in `owner.json`. Git repositories receive a local `.git/info/exclude` entry so the marker does not dirty status. A per-user SQLite transaction serializes acquire, renew, and release so a stale owner cannot overwrite a replacement lock. The same per-user state directory contains the registry used by `list` and `watch`.

This is cooperative coordination, not a security boundary. Filesystems such as NFS, SMB, and cloud-sync folders may weaken atomicity. Use a transactional coordinator with fencing for cross-host deployments or irreversible external operations.
