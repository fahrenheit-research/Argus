"""`argus update` / `argus rollback` — real implementation with brand animation.

Updates BOTH:
  1. The ARGUS project itself  (git pull from your fork's remote)
  2. The embedded fahrenheit-research modules  (re-vendored from upstream
     to keep AgentWire / AgentBrain / AgentMomento current)

Workflow:
  argus update
    1. Snapshot current source to ~/.argus/backups/<timestamp>/
    2. Pull project repo (if it has a git remote)
    3. Re-vendor each fahrenheit-research module from upstream
    4. `uv sync` to pick up any new deps
    5. Run pytest -q
    6. On any failure → auto-restore snapshot, exit non-zero
    7. Success → write new version manifest to ~/.argus/version.json

  argus rollback [--pick]
    Restore the most recent (or chosen) snapshot, re-sync deps, verify.
    A safety-net snapshot is taken before the rollback so it's reversible.

Backups are stored as plain copies in ~/.argus/backups/ — easy to inspect
with `diff -r`, easy to delete, no special tooling.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console  import Console
from rich.live     import Live
from rich.panel    import Panel
from rich.progress import (Progress, SpinnerColumn, BarColumn, TextColumn,
                            TimeElapsedColumn, MofNCompleteColumn)
from rich.prompt   import Prompt
from rich.table    import Table
from rich.text     import Text

from argus import __version__

BACKUPS_DIR = Path(os.path.expanduser("~/.argus/backups"))


# ── Fahrenheit Research modules ──────────────────────────────────────────────
# These are vendored copies in argus/agent{wire,brain,momento}/. On update
# we re-pull the latest from upstream and replace the local copy.

FAHRENHEIT_MODULES = {
    "agentwire":   "https://github.com/fahrenheit-research/AgentWire",
    "agentbrain":  "https://github.com/fahrenheit-research/AgentBrain",
    "agentmomento":"https://github.com/fahrenheit-research/AgentMomento",
}


# ── Brand colors ─────────────────────────────────────────────────────────────
_MAG  = "#FF38D1"
_GOLD = "#FFC247"
_CYAN = "#42E8F5"
_DIM  = "#7A7A7A"


def _find_project_dir() -> Path:
    """Walk up from this file looking for the source-tree pyproject.toml.

    When running from an installed package (UV_NO_EDITABLE=1), __file__ lives
    under site-packages and parents[2] is the wrong place. Walk up until we
    find a directory containing pyproject.toml AND argus/ — that's the
    real source tree. Falls back to ~/Desktop/Argus.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists() and (parent / "argus").is_dir():
            return parent
    fallback = Path.home() / "Desktop/Argus"
    if (fallback / "pyproject.toml").exists():
        return fallback
    return here.parents[2]


PROJECT_DIR = _find_project_dir()
VENV_PATH   = Path(os.path.expanduser("~/Library/Caches/argus/venv"))


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _venv_python() -> str:
    p = VENV_PATH / "bin" / "python"
    return str(p) if p.exists() else sys.executable


def _print_panel(console: Console, *, title: str, body: str, border: str = _MAG) -> None:
    console.print(Panel.fit(body, title=f"[bold {_GOLD}]{title}[/bold {_GOLD}]",
                             border_style=border))


# ── Public CLI entrypoints ───────────────────────────────────────────────────

def run(check: bool = False, channel: str = "stable") -> None:
    """`argus update` — fetch latest, back up, verify, rollback on failure."""
    console = Console()

    _print_panel(console, title="⟨◇⟩ ARGUS Update",
                  body=(f"  [{_DIM}]project[/]   [#F5E6C8]{PROJECT_DIR}[/]\n"
                        f"  [{_DIM}]current[/]   [#F5E6C8]argus {__version__}[/]\n"
                        f"  [{_DIM}]channel[/]   [{_CYAN}]{channel}[/]\n"
                        f"  [{_DIM}]modules[/]   [{_CYAN}]{', '.join(FAHRENHEIT_MODULES)}[/]"))

    is_git = (PROJECT_DIR / ".git").exists()
    plan: list[str] = []

    # ── Phase 1: project repo check ────────────────────────────────────────
    project_behind = 0
    if is_git:
        console.print(f"\n[{_CYAN}]⟨◇⟩[/] [bold]Step 1/5[/] — checking your ARGUS repo…")
        try:
            subprocess.run(["git", "fetch", "--all", "--quiet"],
                           cwd=PROJECT_DIR, capture_output=True, check=True)
            project_behind = _commits_behind()
            if project_behind == 0:
                console.print(f"  [{_GOLD}]✓[/] already up to date with remote")
            else:
                console.print(f"  [{_CYAN}]⟨◇⟩[/] [bold]{project_behind}[/] new commit(s) on remote")
                plan.append(f"pull {project_behind} commit(s)")
        except subprocess.CalledProcessError:
            console.print(f"  [{_DIM}]no remote configured — skipping project pull[/]")
    else:
        console.print(f"\n[{_DIM}]⟨◇⟩ project is not a git repo — skipping project pull[/]")

    # ── Phase 2: check fahrenheit-research modules ────────────────────────
    console.print(f"\n[{_CYAN}]⟨◇⟩[/] [bold]Step 2/5[/] — checking embedded modules…")
    modules_to_update: list[str] = []
    for mod, url in FAHRENHEIT_MODULES.items():
        local  = PROJECT_DIR / "argus" / mod
        remote = _fetch_latest_sha(url)
        local_marker = local / ".vendor.json"
        local_sha = json.loads(local_marker.read_text()).get("sha", "")[:7] \
                     if local_marker.exists() else "(unknown)"

        if remote and not local_marker.exists():
            console.print(f"  [{_CYAN}]⟨◇⟩[/] {mod}: first sync (will vendor {remote[:7]})")
            modules_to_update.append(mod)
        elif remote and remote[:7] != local_sha:
            console.print(f"  [{_CYAN}]⟨◇⟩[/] {mod}: [{_DIM}]{local_sha}[/] → [{_GOLD}]{remote[:7]}[/]")
            modules_to_update.append(mod)
        elif remote:
            console.print(f"  [{_GOLD}]✓[/] {mod}: already at {local_sha}")
        else:
            console.print(f"  [{_DIM}]?[/] {mod}: couldn't reach upstream (skip)")

    if modules_to_update:
        plan.append(f"update {len(modules_to_update)} embedded module(s)")

    if not plan:
        console.print(f"\n[bold {_GOLD}]✓ All up to date. Nothing to do.[/bold {_GOLD}]")
        return

    if check:
        console.print(f"\n[{_DIM}]check-only mode — would: {', '.join(plan)}.[/]")
        console.print(f"[{_DIM}]Run [{_CYAN}]argus update[/] to apply.[/]")
        return

    # ── Phase 3: snapshot ──────────────────────────────────────────────────
    snapshot_id = _now_id()
    snapshot    = BACKUPS_DIR / snapshot_id
    console.print(f"\n[{_CYAN}]⟨◇⟩[/] [bold]Step 3/5[/] — taking backup…")
    with _spinner(console, "snapshotting source tree…"):
        _create_snapshot(snapshot)
    console.print(f"  [{_GOLD}]✓[/] backup at [#F5E6C8]{snapshot}[/]  "
                   f"([{_CYAN}]{_dir_size(snapshot)/1024/1024:.1f} MB[/])")

    # ── Phase 4a: pull project ─────────────────────────────────────────────
    if is_git and project_behind > 0:
        console.print(f"\n[{_CYAN}]⟨◇⟩[/] pulling latest ARGUS…")
        try:
            subprocess.run(["git", "pull", "--ff-only"],
                           cwd=PROJECT_DIR, check=True, capture_output=True)
            console.print(f"  [{_GOLD}]✓[/] fast-forwarded {project_behind} commit(s)")
        except subprocess.CalledProcessError as e:
            console.print(f"  [bold #FF5C5C]✗ pull failed — restoring backup[/bold #FF5C5C]")
            console.print(f"  [{_DIM}]{(e.stderr or b'').decode()[:200]}[/]")
            _restore_snapshot(snapshot)
            raise SystemExit(1)

    # ── Phase 4b: update embedded modules ──────────────────────────────────
    for mod in modules_to_update:
        url = FAHRENHEIT_MODULES[mod]
        console.print(f"\n[{_CYAN}]⟨◇⟩[/] re-vendoring [{_GOLD}]{mod}[/] from upstream…")
        try:
            _vendor_module(mod, url, console)
            console.print(f"  [{_GOLD}]✓[/] {mod} updated")
        except Exception as e:
            console.print(f"  [bold #FF5C5C]✗ {mod} failed: {e}[/bold #FF5C5C]")
            console.print(f"  [{_DIM}]restoring backup…[/]")
            _restore_snapshot(snapshot)
            raise SystemExit(1)

    # ── Phase 5: sync + test ───────────────────────────────────────────────
    console.print(f"\n[{_CYAN}]⟨◇⟩[/] [bold]Step 4/5[/] — syncing dependencies…")
    sync_rc = subprocess.run(
        ["uv", "sync", "--extra", "voice", "--extra", "voice-wake",
         "--extra", "office", "--extra", "cron", "--extra", "vault",
         "--extra", "api", "--reinstall-package", "argus"],
        cwd=PROJECT_DIR,
        env={**os.environ, "UV_NO_EDITABLE": "1",
              "UV_PROJECT_ENVIRONMENT": str(VENV_PATH)},
    ).returncode
    if sync_rc != 0:
        console.print(f"  [bold #FF5C5C]✗ uv sync failed → rolling back[/bold #FF5C5C]")
        _restore_snapshot(snapshot)
        raise SystemExit(1)
    console.print(f"  [{_GOLD}]✓[/] dependencies installed")

    console.print(f"\n[{_CYAN}]⟨◇⟩[/] [bold]Step 5/5[/] — verifying with the test suite…")
    test_rc = subprocess.run(
        [_venv_python(), "-m", "pytest", "-q", "--tb=line"],
        cwd=PROJECT_DIR).returncode
    if test_rc != 0:
        console.print(f"\n  [bold #FF5C5C]✗ tests failed on the new version → rolling back[/bold #FF5C5C]")
        _restore_snapshot(snapshot)
        console.print(f"  [{_DIM}]Backup kept at {snapshot}[/]")
        raise SystemExit(1)
    console.print(f"  [{_GOLD}]✓[/] all tests pass")

    # ── Done — write version manifest ──────────────────────────────────────
    new_ver = _read_version()
    _write_version_manifest(new_ver, snapshot_id, modules_to_update)

    console.print()
    _print_panel(console, title="✓ Update complete", border=_GOLD,
                  body=(f"  [{_DIM}]version[/]   [{_GOLD}]argus {new_ver}[/]\n"
                        f"  [{_DIM}]modules[/]   {len(modules_to_update)} updated\n"
                        f"  [{_DIM}]backup[/]    [#F5E6C8]{snapshot.name}[/]\n"
                        f"  [{_DIM}]rollback[/]  [{_CYAN}]argus rollback[/]"))


def rollback(pick: bool = False) -> None:
    """`argus rollback` — restore most recent backup (or pick one)."""
    console = Console()
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = sorted([p for p in BACKUPS_DIR.iterdir() if p.is_dir()],
                        reverse=True)
    if not snapshots:
        console.print(f"[{_DIM}]⟨◇⟩ no backups available — nothing to roll back to.[/]")
        console.print(f"[{_DIM}]Backups are created automatically when you run "
                       f"[{_CYAN}]argus update[/].[/]")
        return

    # Show available snapshots
    table = Table(title="Available backups", header_style=f"bold {_MAG}",
                   border_style=_DIM)
    table.add_column("idx",  justify="right")
    table.add_column("when", style="#F5E6C8")
    table.add_column("size", justify="right", style=_CYAN)
    for i, s in enumerate(snapshots):
        table.add_row(str(i), s.name, f"{_dir_size(s)/1024/1024:.1f} MB")
    console.print(table)

    target_idx = 0
    if pick:
        target_idx = int(Prompt.ask(f"[{_GOLD}]Restore which backup?[/]", default="0"))
    if not 0 <= target_idx < len(snapshots):
        console.print(f"[bold #FF5C5C]✗ invalid index {target_idx}[/bold #FF5C5C]")
        raise SystemExit(1)

    target = snapshots[target_idx]
    console.print(f"\n[{_CYAN}]⟨◇⟩[/] restoring [#F5E6C8]{target.name}[/]…")

    # Safety net so the rollback itself is reversible
    safety = BACKUPS_DIR / f"{_now_id()}-pre-rollback"
    with _spinner(console, "taking safety-net snapshot…"):
        _create_snapshot(safety)
    console.print(f"  [{_DIM}]safety net: {safety.name}[/]")

    with _spinner(console, "restoring files…"):
        _restore_snapshot(target)

    console.print(f"\n[{_CYAN}]⟨◇⟩[/] syncing dependencies…")
    # All extras — same set as `argus update`. Without these, rollback
    # would silently wipe sounddevice/faster-whisper/python-docx/etc and
    # leave `argus doctor` showing 4 broken checks.
    subprocess.run(
        ["uv", "sync", "--extra", "voice", "--extra", "voice-wake",
         "--extra", "office", "--extra", "cron", "--extra", "vault",
         "--extra", "api", "--reinstall-package", "argus"],
        cwd=PROJECT_DIR,
        env={**os.environ, "UV_NO_EDITABLE": "1",
              "UV_PROJECT_ENVIRONMENT": str(VENV_PATH)},
    )

    new_ver = _read_version()
    _print_panel(console, title="✓ Rolled back", border=_GOLD,
                  body=(f"  [{_DIM}]restored[/]  [{_GOLD}]argus {new_ver}[/]\n"
                        f"  [{_DIM}]from[/]      [#F5E6C8]{target.name}[/]\n"
                        f"  [{_DIM}]undo[/]      [{_CYAN}]argus rollback --pick[/]  "
                        f"(select [{_GOLD}]{safety.name}[/])"))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _spinner(console: Console, label: str) -> Live:
    """Branded spinner with magenta diamond. Use as context manager."""
    p = Progress(SpinnerColumn(spinner_name="dots", style=_MAG),
                 TextColumn(f"[{_DIM}]{label}[/]"),
                 TimeElapsedColumn(),
                 console=console, transient=True)
    p.add_task("", total=None)
    return p


def _commits_behind() -> int:
    """Number of commits HEAD is behind the upstream tracking branch."""
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..@{u}"],
            cwd=PROJECT_DIR, capture_output=True, text=True, check=True)
        return int(r.stdout.strip() or 0)
    except Exception:
        return 0


def _fetch_latest_sha(repo_url: str) -> Optional[str]:
    """Get the latest commit SHA on the default branch without cloning.

    Uses `git ls-remote` — only needs `git` on the path, no SSH/token.
    """
    try:
        r = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True, text=True, timeout=15, check=True)
        return r.stdout.split()[0] if r.stdout else None
    except Exception:
        return None


def _vendor_module(mod: str, repo_url: str, console: Console) -> None:
    """Replace argus/<mod>/ with a fresh shallow clone of the upstream repo.

    We keep ONLY the Python source files — drop .git, tests, docs, examples.
    The original __init__.py is preserved (it has our integration glue).
    Writes a .vendor.json marker so we can detect drift on next update.
    """
    import tempfile

    target_dir = PROJECT_DIR / "argus" / mod
    if not target_dir.exists():
        target_dir.mkdir(parents=True)

    # Save our existing __init__.py (our integration glue)
    init_backup = None
    init_path   = target_dir / "__init__.py"
    if init_path.exists():
        init_backup = init_path.read_text()

    with tempfile.TemporaryDirectory(prefix="argus_vendor_") as tmpdir:
        with _spinner(console, f"  shallow-cloning {repo_url}…"):
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", repo_url, tmpdir],
                check=True, capture_output=True)

        # Get the SHA we just pulled
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmpdir, capture_output=True, text=True).stdout.strip()

        # Pick the right source dir — convention: package name lowercased
        candidates = [Path(tmpdir) / mod, Path(tmpdir) / mod.capitalize(),
                       Path(tmpdir) / "src" / mod]
        src = next((p for p in candidates if p.is_dir()), Path(tmpdir))

        # Clear target (keep dir) and re-copy .py files
        for child in target_dir.iterdir():
            if child.name == "__pycache__":
                shutil.rmtree(child, ignore_errors=True)
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        copied = 0
        for py in src.rglob("*.py"):
            if "test" in py.parts or "example" in py.parts or "_test" in py.name:
                continue
            rel = py.relative_to(src)
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(py, dst)
            copied += 1

        # Restore our integration __init__.py (override the upstream's if any)
        if init_backup is not None:
            init_path.write_text(init_backup)

        # Write the vendor marker
        (target_dir / ".vendor.json").write_text(json.dumps({
            "module":    mod,
            "upstream":  repo_url,
            "sha":       sha,
            "vendored":  datetime.now().isoformat(timespec="seconds"),
            "files":     copied,
        }, indent=2))


def _create_snapshot(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    skip = {".git", ".venv", ".venv 2", ".venv 3", "__pycache__",
             ".pytest_cache", "node_modules", "android", "dist", "build",
             ".mypy_cache", ".ruff_cache"}
    for item in PROJECT_DIR.iterdir():
        if item.name in skip:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True,
                             ignore=shutil.ignore_patterns(
                                 "__pycache__", "*.pyc", ".DS_Store"))
        else:
            shutil.copy2(item, target)


def _restore_snapshot(src: Path) -> None:
    skip = {".git", ".venv", ".venv 2", ".venv 3", "__pycache__",
             ".pytest_cache", "node_modules", "android"}
    for item in src.iterdir():
        target = PROJECT_DIR / item.name
        if item.name in skip:
            continue
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _read_version() -> str:
    try:
        import importlib, argus
        importlib.reload(argus)
        return argus.__version__
    except Exception:
        return __version__


def _write_version_manifest(version: str, snapshot_id: str,
                              modules_updated: list[str]) -> None:
    manifest_path = Path.home() / ".argus" / "version.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    history: list[dict] = []
    if manifest_path.exists():
        try:
            history = json.loads(manifest_path.read_text()).get("history", [])
        except Exception:
            pass

    history.insert(0, {
        "version":          version,
        "updated_at":       datetime.now().isoformat(timespec="seconds"),
        "backup_id":        snapshot_id,
        "modules_updated":  modules_updated,
    })
    history = history[:20]   # cap history size

    manifest_path.write_text(json.dumps({
        "current_version":  version,
        "history":          history,
    }, indent=2))
