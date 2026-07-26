# Project instructions

## Purpose

This repository ships the `project-lock` agent skill and its dependency-free Python command-line tool. Lock compatibility is a public protocol: preserve backward readability when changing `owner.json`.

## Commands

```bash
python -m pytest -q
python -m pytest tests/test_hook.py -q
ruff check scripts tests hooks
ruff format --check scripts tests hooks
uvx --from skills-ref==0.1.1 agentskills validate ../project-lock
python scripts/project-lock.py --help
```

## Rules

- Lock acquisition must remain one atomic `mkdir` operation.
- An elapsed estimate never makes a lock safe to steal automatically.
- Keep runtime dependencies in the Python standard library.
- Do not put machine-specific paths in shipped files.
- Keep `TEST-REPORT.md` current and follow `SMOKE.md` before release.
