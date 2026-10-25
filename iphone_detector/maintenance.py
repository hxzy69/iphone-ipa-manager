"""Conservative local maintenance: preview first, preserve device and recovery state."""

import json
from pathlib import Path
import shutil
import time
import sys

from iphone_detector.paths import DATA_ROOT
from iphone_detector.job_history import read

TERMINAL = {"completed", "cancelled", "acknowledged"}


def cleanup_candidates(root=DATA_ROOT, *, now=None):
    """Only terminal queue/outbox records and old logs; no IPA or receipt deletion."""
    from iphone_detector.worker_registry import active_jobs

    now = time.time() if now is None else now
    root = Path(root).resolve()
    active = {value["job"].resolve() for value in active_jobs(root)}
    pending = read(root / "auto-refresh-state.json").get("pending_job")
    if pending:
        active.add(Path(pending).resolve())
    values = []
    for folder in (root / "queue", root / "discord-outbox"):
        for path in folder.glob("*.json"):
            if path.is_symlink() or folder.is_symlink():
                continue
            value = read(path)
            terminal = (
                value.get("state") in TERMINAL
                if folder.name == "queue"
                else bool(value.get("delivered_at"))
            )
            if terminal and now - path.stat().st_mtime > 90 * 86400:
                values.append(path)
    for parent in (root / "automation-runs", root / "gui-jobs"):
        if parent.is_symlink():
            continue
        for job in parent.glob("*"):
            if job.is_symlink() or not job.is_dir() or job.resolve() in active:
                continue
            if read(job / "result.json").get("ok") is not True:
                continue  # Unresolved jobs retain their diagnostic evidence.
            for path in job.glob("*.log"):
                if not path.is_symlink() and now - path.stat().st_mtime > 30 * 86400:
                    values.append(path)
    # Remove obsolete download ZIPs, never installed EXEs or rollback versions.
    protected = [Path(sys.executable).resolve()]
    for filename, key in (
        ("prepared-update.json", "plan"),
        ("previous-version.json", "executable"),
    ):
        value = read(root / filename).get(key)
        if isinstance(value, str) and value:
            protected.append(Path(value).resolve())
    updates = root / "updates"
    if not updates.is_symlink():
        for folder in updates.glob("*"):
            if (
                folder.is_symlink()
                or not folder.is_dir()
                or any(path.is_relative_to(folder.resolve()) for path in protected)
            ):
                continue
            for path in folder.iterdir():
                if (
                    not path.is_symlink()
                    and path.is_file()
                    and (
                        path.name == "SHA256SUMS.txt"
                        or (path.name.startswith("iPhoneIPAManager-") and path.suffix == ".zip")
                    )
                    and now - path.stat().st_mtime > 7 * 86400
                ):
                    values.append(path)
    return values


def preview(root=DATA_ROOT):
    paths = cleanup_candidates(root)
    free = shutil.disk_usage(Path(root).anchor).free
    return {
        "files": len(paths),
        "reclaimable_bytes": sum(p.stat().st_size for p in paths),
        "free_bytes": free,
    }


def clean(root=DATA_ROOT):
    from iphone_detector.run_lock import RunLock
    from iphone_detector.job_queue import guard

    lock = RunLock()
    if not lock.acquire():
        raise ValueError("Wait for active work before cleaning old files.")
    try:
        with guard():
            return _clean_locked(root)
    finally:
        lock.close()


def _clean_locked(root):
    root = Path(root).resolve()
    removed = 0
    for path in cleanup_candidates(root):
        # Validate every final target on Windows. Never recursively remove a folder.
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved == root or path.is_symlink():
            raise ValueError("Maintenance target is outside the application data directory.")
        path.unlink()
        removed += 1
    return removed


def quarantine_invalid_config(root=DATA_ROOT):
    """Explicit local repair preserves the original bytes instead of guessing settings."""
    from iphone_detector.run_lock import RunLock

    root = Path(root).resolve()
    path = root / "config.json"
    lock = RunLock()
    if not lock.acquire():
        raise ValueError("Wait for active work before repairing settings.")
    try:
        if path.is_symlink():
            raise ValueError("Settings contain a symbolic link. Inspect it locally.")
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                from iphone_detector.config import validate_config

                try:
                    validate_config(value)
                except ValueError:
                    pass
                else:
                    raise ValueError(
                        "Settings are readable; restore a configuration backup to change them."
                    )
        except json.JSONDecodeError, UnicodeError:
            pass
        destination = root / "backups" / f"invalid-settings-{time.time_ns()}.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.replace(destination)
        return destination
    finally:
        lock.close()
