"""Verified account outcomes, bounded history and explicit partial-run recovery."""

from datetime import datetime
import json
import math
import time

from iphone_detector.storage import write_json
from iphone_detector.job_history import read


def jobs(root, limit=100):
    entries = []
    for parent in (root / "automation-runs", root / "gui-jobs"):
        if parent.is_dir():
            for path in parent.iterdir():
                try:
                    if path.is_dir() and not path.is_symlink():
                        entries.append((path.stat().st_mtime, path))
                except OSError:
                    continue
    return [path for _, path in sorted(entries, reverse=True)[:limit]]


def record(job, request, number, operation="install"):
    entry = next((a for a in request.get("accounts", []) if a.get("account") == number), None)
    if entry is None or operation not in ("install", "refresh"):
        raise ValueError("Completion does not match the requested account.")
    evidence = read(job / f"profile-refresh-{number}.json")
    expires = None
    if operation == "refresh" and evidence.get("profile_readback_verified") is True:
        values = [item.get("after") for item in evidence.get("expiration_changes", [])]
        try:
            expires = min(datetime.fromisoformat(value).timestamp() for value in values)
        except ValueError, TypeError:
            pass
    receipt = {
        "schema_version": 1,
        "verified": True,
        "account": number,
        "serial": request.get("serial"),
        "selection": entry,
        "operation": operation,
        "at": time.time(),
        "profile_expires_at": expires,
    }
    write_json(job / f"completed-{number}.json", receipt)
    return receipt


def collect_native(job):
    """The native completion event is emitted only after install acknowledgement."""
    request = read(job / "backend-job.json")
    if request.get("prepare_only") or request.get("check_only"):
        return
    try:
        lines = (job / "backend-events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            event = json.loads(line)
            number = event.get("account")
            if event.get("stage") != "completed_account" or type(number) is not int:
                continue
            path = job / f"completed-{number}.json"
            if not path.exists():
                # Remote installs persist richer, verified receipts themselves.
                record(job, request, number)
        except ValueError, TypeError, AttributeError:
            continue


def matching(receipt, request, entry):
    from iphone_detector.wifi import canonical_serial

    return (
        receipt.get("verified") is True
        and canonical_serial(receipt.get("serial") or "")
        == canonical_serial(request.get("serial") or "")
        and receipt.get("selection") == entry
        and type(receipt.get("at")) in (int, float)
        and math.isfinite(receipt["at"])
        and 0 <= receipt["at"] <= time.time() + 5
    )


def retry_plan(job, config):
    from iphone_detector.background import make_job
    from iphone_detector.worker_registry import active_jobs
    from iphone_detector.wifi import canonical_serial

    if any(value["job"].resolve() == job.resolve() for value in active_jobs(job.parent.parent)):
        raise ValueError("This run is still active. Stop it and wait for cleanup first.")
    if not (job / "result.json").is_file():
        raise ValueError("This run has no final result. Inspect its state before retrying.")
    original = read(job / "selection.json") or read(job / "backend-job.json")
    entries = original.get("accounts", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("This run has no usable account snapshot.")
    numbers = [entry.get("account") for entry in entries]
    if not numbers or any(type(n) is not int or not 1 <= n <= 25 for n in numbers):
        raise ValueError("This run has no usable account snapshot.")
    current = make_job(config, "Network", accounts=[n - 1 for n in numbers])
    if (
        not isinstance(original.get("serial"), str)
        or canonical_serial(original["serial"]) != canonical_serial(current["serial"])
        or original.get("accounts") != current.get("accounts")
    ):
        raise ValueError(
            "The phone, account or IPA changed. Start a new run with the current assignments."
        )
    remaining = [
        entry["account"] - 1
        for entry in current["accounts"]
        if not matching(read(job / f"completed-{entry['account']}.json"), current, entry)
    ]
    if not remaining:
        raise ValueError("Every requested account has a verified completion; nothing to retry.")
    return remaining


def account_status(root, config):
    from iphone_detector.background import make_job
    from iphone_detector.settings import get_workflow

    rows = {}
    # Missing IPAs should not prevent a dashboard from opening.
    request = {"serial": config.get("device", {}).get("serial"), "accounts": []}
    for index, _ in enumerate(get_workflow(config)):
        try:
            request["accounts"].extend(make_job(config, "Network", accounts=[index])["accounts"])
        except OSError, ValueError:
            continue
    for job in jobs(root):
        for entry in request["accounts"]:
            number = entry["account"]
            receipt = read(job / f"completed-{number}.json")
            if matching(receipt, request, entry):
                if number not in rows or receipt["at"] > rows[number]["at"]:
                    rows[number] = receipt
    return rows
