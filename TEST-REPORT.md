skills-project-lock test report - 2026-08-04T00:49:48Z
========================================================

Git:      fix/scratch-carveout-and-ecosystem @ 515dc7f
Status:   PASS
Tests:    79 passed, 0 failed, 0 skipped
Coverage: 337/337 statements, 112/112 branches (100%) on scripts/project_lock
Lint:     ruff check: clean
          ruff format --check: clean
          markdownlint-cli2: 0 findings
          agentskills validate (skills-ref 0.1.1): valid

Commands
--------

```bash
python -m pytest -q
ruff check scripts tests hooks
ruff format --check scripts tests hooks
npx --yes markdownlint-cli2
uvx --from skills-ref==0.1.1 agentskills validate
```
