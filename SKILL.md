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

## Enforcement

Installations may wire `hooks/pre_tool_use.py` as a Claude Code `PreToolUse` hook (matcher `Edit|Write|NotebookEdit|Bash`). It denies file edits governed by another session's lock and denies unlocked edits with the exact acquire command to run (including `--session` when the payload carries a session id). Bash commands are split into segments on `;`, `&`, `|`, and newlines, and each segment is classified independently. Bash calls are denied only when the session's cwd sits in a foreign jurisdiction and the command is not read-only, or when a non-read-only command references an absolute path (including `~`- or `$VAR`-prefixed forms, expanded before comparison) under a registered foreign lock. Modes: `PROJECT_LOCK_ENFORCE=deny` (default), `warn`, `off`. The hook fails open on its own errors.

Enforcement only ever applies inside a real project root. An unlocked `Edit`/`Write`/`NotebookEdit` is denied with an acquire recipe *only* when the target sits somewhere under an actual Git checkout (there is a `.git` at or above it). A target with no `.git` anywhere above it — a scratch file under `$TMPDIR`, `/tmp`, or any other directory that was never a Git checkout — is always allowed, since there is no project to coordinate. This carve-out is checked with `has_git_ancestor()`, distinct from the deny path for a real, already-registered lock: a project that happens to be checked out under a temp mount still has a `.git` of its own and is still enforced normally.

Enforcement does not cover Bash writes that reach a foreign jurisdiction from outside via relative or quoted paths. Absolute paths containing spaces also escape the token scan, since a quoted space is indistinguishable from a token boundary. Think before you Bash: hold the locks of every jurisdiction your command spans, especially for recursive operations (`rm -rf`, `git clean`, formatter sweeps, `git add -A`).

Unlocked Bash is allowed by design: acquiring a lock itself requires running a command, so the unlocked-write deny only applies to file tools (`Edit`/`Write`/`NotebookEdit`). Bash enforcement engages only against foreign jurisdictions, never against the absence of a lock.

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

Jurisdiction is the nearest enclosing Git worktree: a lock governs everything under its root except nested worktrees (their `.git` boundary stops it), and an ancestor checkout's lock never reaches inside a worktree checked out as a subfolder. `check` reports related ancestor and nested locks so subtree-wide operations can be cleared manually first.

Acquisition atomically creates `<root>/.agent-lock/`; metadata lives in `owner.json`. Git repositories receive a local `.git/info/exclude` entry so the marker does not dirty status. A per-user SQLite transaction serializes acquire, renew, and release so a stale owner cannot overwrite a replacement lock. The same per-user state directory contains the registry used by `list` and `watch`.

This is cooperative coordination, not a security boundary. Filesystems such as NFS, SMB, and cloud-sync folders may weaken atomicity. Use a transactional coordinator with fencing for cross-host deployments or irreversible external operations.

`acquire`, `renew`, and `release` all serialize through one global SQLite file in the per-user state directory (`mutation_guard`), regardless of which project each call targets. This is a same-user global critical section: two lock mutations for two *different* projects on the same machine still take turns through that one file. In practice each mutation holds it only briefly, so this is not a throughput concern, but it means lock mutations across all of a user's projects are never truly concurrent.

## Works with

- **fleet-orchestration** treats this skill as its pre-flight check: before dispatching agents across repos, it runs `project-lock.py check <repo>` and follows the reported advice, falling back to ad hoc git heuristics only when this skill isn't installed.
- **unity-batchmode-worktree** acquires this lock on the agent's paired worktree before writing, alongside its own `.claude-reserved` pool-slot marker (that marker tracks pool membership; this lock coordinates the writes).

Both are consumers layered on top of this skill, not alternative locking mechanisms — this skill is the canonical write-coordination primitive.
