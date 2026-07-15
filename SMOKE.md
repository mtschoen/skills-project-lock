# Smoke Test

## Floor - does it run?

```bash
python scripts/project-lock.py --help
```

## Bar - does cooperative locking work?

In a disposable Git repository, acquire a lock with `--json`, confirm `check` exits 3 and identifies the owner and reason, release it with the returned lock ID, then confirm `check` prints `FREE`.

## Cleanup

Release the lock before deleting the disposable repository. Force-clear only if the smoke process failed before capturing its lock ID.
