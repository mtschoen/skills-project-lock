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

Installations may wire `hooks/pre_tool_use.py` as `PreToolUse` hook (matcher `Edit|Write|NotebookEdit|Bash`). It denies file edits governed by another session's lock and denies unlocked edits with the exact acquire command to run (including `--session` when the payload carries a session id). Bash commands are split into segments on `;`, `&`, `|`, and newlines, and each segment is classified independently. Bash calls are denied only when the session's cwd sits in a foreign jurisdiction and the command is not read-only, or when a non-read-only command references an absolute path (including `~`- or `$VAR`-prefixed forms, expanded before comparison) under a registered foreign lock. Modes: `PROJECT_LOCK_ENFORCE=deny` (default), `warn`, `off`. The hook fails open on its own errors. If the hook is not wired and this skill arrived as part of a skill pack, check whether the pack's installer provides a hook offer flow (a command that checks hook registration state and records the user's yes / no / remind-me-later decision) and use that flow to prompt the user instead of suggesting manual registration, so the user is asked at most once.

**Git administration state** answers to the *governing checkout* rather than to whichever lock sits above the path. Refs, config, and the worktree registry are shared by every worktree, so an ordinary worktree lock cannot speak for them. The governing checkout is the one whose `.git` the path belongs to, which for repository-wide state is the main worktree; holding its lock means being in charge of the repository. A direct file-tool write is:

- **allowed** when this session holds the governing checkout's lock;
- **allowed** when nothing anywhere in the repository is locked, since there is no one to coordinate with;
- **denied** when another session holds the governing checkout;
- **denied** when the governing checkout is unlocked but another worktree of the repository is locked, so a session working in a linked worktree cannot rewrite shared state out from under whoever else is active. Claim the governing checkout first.

There is deliberately no separate override: acquiring that lock *is* the way in, which keeps the authority explicit and visible to other agents. That matters because Git offers no command for some legitimate edits - `.git/info/exclude` and `.git/hooks/*` have no porcelain equivalent, and a `config` too malformed for Git to parse cannot be repaired with `git config`.

The rule covers the common and private Git directories (`--git-dir`, `--git-common-dir`) and the `.git` marker at a worktree root, whose file form repoints the whole worktree. Bash is deliberately excluded: Git itself writes to `.git` constantly, and a word-level classifier cannot separate that from a raw clobber. The check runs only when a path component ends in `.git`, so ordinary content edits pay no subprocess cost.

Enforcement of an existing lock applies everywhere: any `.agent-lock/` marker governs its jurisdiction whether or not that jurisdiction is a Git checkout. The only carve-out is for *unlocked* writes with nothing registered anywhere above the target. Those are denied with an acquire recipe by default, including in non-Git project directories, since agents still need to coordinate writes there, *except* when the target is true scratch: no `.git` anywhere above it (checked with `has_git_ancestor()`) **and** it sits under a recognized system temp location (`$TMPDIR` or `tempfile.gettempdir()` everywhere; also `/tmp`, `/private/tmp`, and `/var/folders` off Windows, checked with `is_under_temp_dir()`). Both conditions must hold: a Git checkout that happens to live under a temp mount (e.g. a test fixture under `/tmp`) still has its own `.git` and stays enforced normally.

Known limitations of the scratch carve-out: a stray `.git` directory above a temp root (e.g. an accidentally created empty `~/.git`) makes `has_git_ancestor()` true for the whole temp tree beneath it, disabling the carve-out there. A symlink inside a real repo that resolves into a temp root is treated as scratch, since path resolution happens before the temp-root comparison. (On the coordination model's limits generally, see "Protocol and limitations" below.)

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
python <script> release <path> --force \
  --expect-lock-id <the id you verified> \
  --reason "owner session exited; verified pid gone"
```

Both flags are required. `--reason` is recorded; `--expect-lock-id` is a compare-and-swap. Verifying that a lock is abandoned and then clearing it are two steps, and the lock can turn over in between: naming the id you verified makes the override fail rather than silently discard a replacement. A lock whose `owner.json` is unreadable has no id to compare, so there `--reason` alone is the gate.

Every force-clear is appended to `audit.jsonl` in the per-user state directory before the lock is removed, so an override cannot succeed unaudited. A trail that cannot be written fails the override and leaves the lock standing.

`check` reports `owner pid: <pid> (<state>)`, where state is one of:

- **gone** - no such process, or the pid belongs to a *different* process than the one nominated. Strong evidence of abandonment, and the evidence a force-clear wants.
- **running** - the nominated process is alive, confirmed against its recorded start identity.
- **running-unverified** - something holds that pid, but its identity could not be confirmed. Treat as running.
- **unknown** - no process was nominated (the default), the lock was taken on another host, or this platform cannot answer. Start identity is read on Linux and Windows only.

Liveness is **opt-in**, and `unknown` is the default for good reason. Pass `--owner-pid` at acquire time only for a process that outlives the command:

```bash
python <script> acquire <path> --reason "..." --duration 30m --owner-pid $AGENT_SESSION_PID
```

The `acquire` command's own process exits the moment it writes the marker, so recording *it* would report `gone` for every healthy lock and invite force-clearing live work. Nominate the agent session or supervising process, or nominate nothing.

On Windows, pass a real Win32 pid. `$$` inside Git Bash or MSYS is that shell's own emulated pid from a separate namespace, and probing it reports `gone` for a perfectly live shell. Python's `os.getpid()` and PowerShell's `$PID` give the Win32 value.

Never force-clear merely because `expected_until` passed. A dead owner process is evidence for your judgement, never an automatic verdict: `recommendation` deliberately ignores it.

## Protocol and limitations

Jurisdiction is the nearest enclosing Git worktree, and covers that worktree's *content* only. Repository-wide state (refs, config, the worktree registry) is shared by every worktree and belongs to the governing checkout, normally the main worktree; see Enforcement above. A lock governs everything under its root except nested worktrees (their `.git` boundary stops it), and an ancestor checkout's lock never reaches inside a worktree checked out as a subfolder. `check` reports related ancestor and nested locks so subtree-wide operations can be cleared manually first.

Acquisition atomically creates `<root>/.agent-lock/`; metadata lives in `owner.json`. Git repositories receive a local `.git/info/exclude` entry so the marker does not dirty status. A per-user SQLite transaction serializes acquire, renew, and release so a stale owner cannot overwrite a replacement lock. The same per-user state directory contains the registry used by `list` and `watch`.

This is cooperative coordination, not a security boundary. Filesystems such as NFS, SMB, and cloud-sync folders may weaken atomicity. Use a transactional coordinator with fencing for cross-host deployments or irreversible external operations.

A `PreToolUse` hook governs the writes a harness represents in tool input, and nothing else. It cannot govern:

- arbitrary shell redirection, or paths a command computes at runtime;
- writes by Python, Node, build tools, test runners, package managers, formatters, or editors;
- subprocesses and background jobs;
- file descriptors opened before the decision, or writable memory mappings;
- link and rename races between the allow decision and the write;
- another terminal, IDE, human, or agent outside the interceptor.

An allow decision is therefore a point-in-time cooperative snapshot, not protection against a racing writer. Do not read it as stronger than that.

`acquire`, `renew`, and `release` all serialize through one global SQLite file in the per-user state directory (`mutation_guard`), regardless of which project each call targets. This is a same-user global critical section: two lock mutations for two *different* projects on the same machine still take turns through that one file. In practice each mutation holds it only briefly, so this is not a throughput concern, but it means lock mutations across all of a user's projects are never truly concurrent.

## Works with

- **fleet-orchestration** treats this skill as its pre-flight check: before dispatching agents across repos, it runs `project-lock.py check <repo>` and follows the reported advice, falling back to ad hoc git heuristics only when this skill isn't installed.
- **unity-batchmode-worktree** acquires this lock on the agent's paired worktree before writing, alongside its own `.worktree-reserved` pool-slot marker (that marker tracks pool membership; this lock coordinates the writes). The marker is self-documenting rather than a bare touch file: its contents record `reserved-by` (the claiming harness and session id, or `user@host` for a manual reservation) and `reserved-at` (an ISO 8601 UTC timestamp), with a `stale-after` window (24h) that is only an operator hint for reaping the *marker* - exactly like `expected_until` above, an elapsed `stale-after` does not by itself make the lock safe to force-clear; that still requires independent verification. Full field spec lives in unity-batchmode-worktree's SKILL.md; this repo only cross-references it.

Both are consumers layered on top of this skill, not alternative locking mechanisms: this skill is the canonical write-coordination primitive.
