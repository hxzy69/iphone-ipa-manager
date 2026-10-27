"""Verified account outcomes, bounded history and explicit partial-run recovery."""

from datetime import datetime
import json
import math
import time

from iphone_detector.storage import finite_number, write_json
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


def selection_matches(left, right, *, content=True):
    from iphone_detector.wifi import canonical_serial

    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    if any(
        not isinstance(value.get("serial"), str) or not value["serial"] for value in (left, right)
    ):
        return False
    serial = canonical_serial(left["serial"])
    if not serial or serial != canonical_serial(right["serial"]):
        return False
    first, second = left.get("accounts"), right.get("accounts")
    if not isinstance(first, list) or not isinstance(second, list) or len(first) != len(second):
        return False
    if not 1 <= len(first) <= 25:
        return False
    for selection in (first, second):
        seen = set()
        for entry in selection:
            if (
                not isinstance(entry, dict)
                or type(entry.get("account")) is not int
                or not 1 <= entry["account"] <= 25
                or entry["account"] in seen
                or any(
                    not isinstance(entry.get(key), str) or not entry[key].strip()
                    for key in ("email", "ipa")
                )
            ):
                return False
            seen.add(entry["account"])
            identity = entry.get("file_identity")
            if not isinstance(identity, dict) or any(
                type(identity.get(key)) is not int or identity[key] < 0
                for key in ("size", "mtime_ns")
            ):
                return False
            if content and (
                not isinstance(identity.get("sha256"), str)
                or len(identity["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in identity["sha256"])
            ):
                return False
    for a, b in zip(first, second):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        if any(a.get(key) != b.get(key) for key in ("account", "email", "ipa")):
            return False
        x, y = a.get("file_identity", {}), b.get("file_identity", {})
        if not isinstance(x, dict) or not isinstance(y, dict):
            return False
        keys = ("size", "mtime_ns", "sha256") if content else ("size", "mtime_ns")
        if any(key not in x or key not in y or x[key] != y[key] for key in keys):
            return False
    return bool(first)


def verified_job(job, result, config=None):
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config

    try:
        current = make_job(config if config is not None else load_config() or {}, "Network")
        original = read(job / "selection.json") or read(job / "backend-job.json")
        return (
            result.get("ok") is True
            and type(result.get("completed_accounts")) is int
            and result["completed_accounts"] == len(current["accounts"])
            and not result.get("prepared_only")
            and selection_matches(original, current)
        )
    except OSError, ValueError, TypeError, KeyError:
        return False


def index_path(root, serial, entry):
    import hashlib
    from iphone_detector.wifi import canonical_serial

    key = canonical_serial(serial or "") + ":" + entry["email"].casefold()
    return root / "account-outcomes" / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def record(job, request, number, operation="install"):
    entry = next((a for a in request.get("accounts", []) if a.get("account") == number), None)
    if entry is None or operation not in ("install", "refresh"):
        raise ValueError("Completion does not match the requested account.")
    evidence = read(job / f"profile-refresh-{number}.json")
    expires = None
    if operation == "refresh" and evidence.get("profile_readback_verified") is True:
        try:
            changes = evidence.get("expiration_changes")
            if not isinstance(changes, list) or not changes:
                raise ValueError("Missing profile expiration evidence")
            values = []
            for item in changes:
                value = datetime.fromisoformat(item["after"])
                if value.tzinfo is None:
                    raise ValueError("Profile expiration has no timezone")
                timestamp = value.timestamp()
                if not math.isfinite(timestamp) or timestamp < 0:
                    raise ValueError("Invalid profile expiration")
                values.append(timestamp)
            expires = min(values)
        except ValueError, TypeError, KeyError, OverflowError, OSError:
            # Completion still happened. Invalid optional metrics must not erase
            # that receipt or invent a later renewal deadline.
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
    if job.parent.name in ("automation-runs", "gui-jobs"):
        write_json(index_path(job.parent.parent, request.get("serial"), entry), receipt)
    return receipt


def collect_native(job):
    """The native completion event is emitted only after install acknowledgement."""
    request = read(job / "backend-job.json")
    if request.get("prepare_only") or request.get("check_only"):
        return
    try:
        # A damaged line must not hide earlier valid completion events or crash
        # recovery. Replacement characters make malformed JSON lines fail closed.
        lines = (
            (job / "backend-events.jsonl")
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        )
    except OSError:
        return
    for line in lines:
        try:
            if "\ufffd" in line:
                continue  # Never trust an event containing undecodable bytes.
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


def matching(receipt, request, entry, *, content=True):
    from iphone_detector.wifi import canonical_serial

    if not isinstance(receipt, dict) or not isinstance(request, dict):
        return False
    if any(
        not isinstance(value.get("serial"), str) or not value["serial"]
        for value in (receipt, request)
    ):
        return False
    return (
        receipt.get("verified") is True
        and canonical_serial(receipt.get("serial") or "")
        == canonical_serial(request.get("serial") or "")
        and selection_matches(
            {"serial": receipt.get("serial"), "accounts": [receipt.get("selection", {})]},
            {"serial": request.get("serial"), "accounts": [entry]},
            content=content,
        )
        and finite_number(receipt.get("at"))
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


def account_status(root, config, *, verify_content=True):
    from iphone_detector.background import make_job
    from iphone_detector.settings import get_workflow

    rows = {}
    # Missing IPAs should not prevent a dashboard from opening.
    request = {"serial": config.get("device", {}).get("serial"), "accounts": []}
    for index, _ in enumerate(get_workflow(config)):
        try:
            request["accounts"].extend(
                make_job(config, "Network", accounts=[index], verify_content=verify_content)[
                    "accounts"
                ]
            )
        except OSError, ValueError:
            continue
    for entry in request["accounts"]:
        receipt = read(index_path(root, request.get("serial"), entry))
        if matching(receipt, request, entry, content=verify_content):
            rows[entry["account"]] = receipt
    indexed = set(rows)
    # A crash can occur after a job receipt is durable but before its index is
    # updated. Reconcile history even when an older valid index already exists.
    for job in jobs(root, limit=None):
        for entry in request["accounts"]:
            number = entry["account"]
            receipt = read(job / f"completed-{number}.json")
            if matching(receipt, request, entry, content=verify_content):
                if number not in rows or receipt["at"] > rows[number]["at"]:
                    rows[number] = receipt
    for entry in request["accounts"]:
        if entry["account"] in rows and entry["account"] not in indexed:
            write_json(index_path(root, request.get("serial"), entry), rows[entry["account"]])
    return rows
