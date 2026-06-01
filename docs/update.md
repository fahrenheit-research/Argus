# ARGUS Update + Rollback

`argus update` keeps ARGUS itself current. `argus rollback` undoes it.

Both commands operate on the **project source tree** (the cloned git repo)
and the **dependency venv**. Your data (`~/.argus/*.db`, `~/argus-workspace/`)
is never touched.

## Update

```bash
argus update --check       # report what would change; do nothing
argus update               # pull latest, sync deps, run tests; auto-rollback on failure
```

### What it does, in order

1. **Check the project repo**. If it has a git remote, fetch and report
   how many commits behind. Silent skip if no repo / no remote.
2. **Check embedded fahrenheit-research modules**. For each of
   `agentwire`, `agentbrain`, `agentmomento`, do a `git ls-remote` to
   compare the upstream HEAD SHA against the vendored copy's
   `.vendor.json` marker. Report which modules need re-vendoring.
3. **Snapshot** the entire source tree to `~/.argus/backups/<YYYYMMDD-HHMMSS>/`.
   Excludes `.git/`, `.venv*/`, `__pycache__/`, `android/`, `node_modules/`,
   `dist/`, `build/`. Typical snapshot size: 2-5 MB.
4. **Pull project repo** with `--ff-only` (refuses to merge — local
   edits that conflict trigger an auto-rollback).
5. **Re-vendor outdated modules**. For each one out-of-date, shallow-clone
   the upstream, copy the `.py` files into `argus/<module>/`, preserve our
   integration `__init__.py`, write a fresh `.vendor.json` marker.
6. **Sync dependencies** via `uv sync` with all default extras.
7. **Run the test suite** (`pytest -q`). If any test fails, ARGUS
   **automatically rolls back** to the backup and exits non-zero.
8. **Write version manifest** to `~/.argus/version.json` (history of last
   20 updates with their backup IDs and module SHAs).
9. **Print success panel** + how to roll back manually if needed.

### Failure modes

| Situation | Behavior |
|---|---|
| Not a git repo | Silent skip ("nothing to update") |
| No remote configured | Silent skip |
| Already up to date | "✓ already up to date" |
| Local changes block fast-forward | **Auto-rollback** + non-zero exit |
| `uv sync` fails | **Auto-rollback** + non-zero exit |
| Tests fail on the new version | **Auto-rollback** + non-zero exit |
| All good | Success panel + backup ID for manual rollback |

## Rollback

```bash
argus rollback             # restore the MOST RECENT backup
argus rollback --pick      # show the list, pick one
```

`rollback` also takes a **safety-net snapshot first** — so if you accidentally
roll back too far, you can `argus rollback --pick` and select the safety-net
snapshot to go back to where you were.

## Storage

```
~/.argus/backups/
├── 20260601-143012/                  ← snapshot taken before an update
│   ├── argus/
│   ├── tests/
│   ├── pyproject.toml
│   └── ...
├── 20260601-200505-pre-rollback/     ← safety net before a rollback
└── 20260530-091200/                  ← previous update's snapshot
```

Snapshots are kept indefinitely. Clean up old ones manually:

```bash
ls ~/.argus/backups/                          # list
rm -rf ~/.argus/backups/20260530-091200       # delete one
```

## When to use

- **Pull a hotfix**: `argus update`
- **Verify before updating**: `argus update --check`
- **A new release broke something**: `argus rollback`
- **You experimented locally and want to revert**: `argus rollback --pick`
