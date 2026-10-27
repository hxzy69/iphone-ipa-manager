"""Durable FIFO requests, one worker at a time, with fail-closed crash recovery."""

from contextlib import contextmanager
import json
import logging
import os
import subprocess
import time
import uuid

import psutil

from iphone_detector.paths import DATA_ROOT
from iphone_detector.storage import finite_number, write_json
from iphone_detector.run_lock import RunLock

ROOT = DATA_ROOT / "queue"
STATES = {"queued", "running", "completed", "failed", "interrupted", "cancelled", "acknowledged"}
LOG = logging.getLogger("iphone-detector.queue")


class QueueBusy(ValueError):
    """Transient store contention; never treated as corrupt queue data."""


@contextmanager
def guard():
    lock = RunLock()
    lock.name = "Local\\iPhoneIPACommunityQueueStore"
    if not lock.acquire():
        raise QueueBusy("The queue is being changed. Try again.")
    try:
        yield
    finally:
        lock.close()


def entries():
    if not ROOT.exists():
        return []
    result = []
    for path in sorted(ROOT.glob("*.json")):
        if path.is_symlink():
            raise ValueError(
                "Queue state contains a symbolic link; inspect it locally before continuing."
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("id") != path.stem
                or value.get("state") not in STATES
                or value.get("mode") not in ("install", "refresh")
                or not isinstance(value.get("selection"), dict)
                or not isinstance(value["selection"].get("serial"), str)
                or not value["selection"]["serial"]
                or not isinstance(value["selection"].get("accounts"), list)
                or not 1 <= len(value["selection"]["accounts"]) <= 25
                or any(
                    not isinstance(a, dict)
                    or type(a.get("account")) is not int
                    or not 1 <= a["account"] <= 25
                    or not isinstance(a.get("ipa"), str)
                    or not isinstance(a.get("email"), str)
                    or not isinstance(a.get("file_identity"), dict)
                    for a in value["selection"]["accounts"]
                )
            ):
                raise ValueError("Invalid queue record")
        except OSError, ValueError:
            raise ValueError(
                "Queue state is unreadable. Inspect the queue folder before continuing."
            ) from None
        result.append(value)
    return result


def pending():
    return [entry for entry in entries() if entry["state"] in ("queued", "running")]


def enqueue(mode="install", accounts=None, *, expected_email=None):
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config

    if mode not in ("install", "refresh") or (mode == "refresh" and accounts is not None):
        raise ValueError("Choose install, or refresh for all accounts.")
    selection = make_job(load_config() or {}, "Network", accounts=accounts)
    if expected_email is not None and (
        accounts is None
        or len(selection["accounts"]) != 1
        or selection["accounts"][0]["email"] != expected_email
    ):
        raise ValueError("The selected account changed. Refresh the dashboard and select it again.")
    with guard():
        from iphone_detector.job_history import read

        update = read(DATA_ROOT / "update-state.json")
        if (
            update.get("phase") in ("starting", "close_gui", "migrating")
            and type(update.get("at")) in (int, float)
            and update["at"] > time.time() - 600
        ):
            raise ValueError(
                "An app update is being activated. Queue new requests after it finishes."
            )
        if len(pending()) >= 50:
            raise ValueError("The queue already has 50 pending requests.")
        identifier = str(time.time_ns()) + "-" + uuid.uuid4().hex[:8]
        entry = {
            "id": identifier,
            "state": "queued",
            "mode": mode,
            "created_at": time.time(),
            "selection": {key: selection[key] for key in ("serial", "accounts")},
        }
        write_json(ROOT / (identifier + ".json"), entry)
    ensure_started()
    from iphone_detector.job_history import read

    try:
        enabled = read(DATA_ROOT / "queue-settings.json").get("startup", True)
        configure_startup(enabled)
        entry["startup_registered"] = enabled
    except Exception:
        entry["startup_registered"] = False
    return entry


def configure_startup(enabled):
    from iphone_detector.startup_task import configure
    from iphone_detector.discord_bot import command

    if type(enabled) is not bool:
        raise ValueError("Queue startup must be enabled or disabled.")
    configure(enabled, command(["--queue-runner"]), name="iPhone IPA Community Queue")
    write_json(DATA_ROOT / "queue-settings.json", {"startup": enabled})


def change(identifier, action):
    with guard():
        values = entries()
        entry = next((item for item in values if item["id"] == identifier), None)
        if entry is None:
            raise ValueError("Choose an existing queue request.")
        if action == "cancel" and entry["state"] == "queued":
            entry["state"] = "cancelled"
        elif action == "dismiss" and entry["state"] in ("failed", "interrupted"):
            entry["state"] = "acknowledged"
        else:
            raise ValueError(
                "Only waiting requests can be cancelled; dismiss failed/interrupted requests to resume the queue."
            )
        write_json(ROOT / (identifier + ".json"), entry)
    ensure_started()
    return entry


def alive(entry):
    # JSON permits NaN, and bool is an int subclass. Neither is process identity.
    if (
        type(entry.get("pid")) is not int
        or not 0 < entry["pid"] <= 0xFFFFFFFF
        or not finite_number(entry.get("process_created"))
        or entry["process_created"] <= 0
    ):
        return False
    try:
        process = psutil.Process(entry["pid"])
        return process.is_running() and abs(process.create_time() - entry["process_created"]) < 0.01
    except psutil.Error, KeyError, TypeError, ValueError:
        return False


def failure_label(entry):
    return (
        {
            "selection-changed": "Assignment or file changed",
            "worker-launch-failed": "Worker could not start",
            "worker-identity-unavailable": "Process tracking unavailable",
            "job-storage-unavailable": "Job storage unavailable",
            "completion-unverified": "Completion could not be verified",
            "worker-interrupted": "Worker interrupted",
        }.get(entry.get("failure_code"), "")
        if isinstance(entry.get("failure_code"), str)
        else ""
    )


def report_failure(entry):
    """Persist one actionable notification; delivery retries never replay a job."""
    from iphone_detector.discord_outbox import enqueue

    messages = {
        "selection-changed": "The phone, account or IPA changed after this request was queued. Select the current file and account and queue a new request.",
        "worker-launch-failed": "Windows could not start the queued worker. No device work was started by this request.",
        "worker-identity-unavailable": "The worker started but Windows process tracking failed. Inspect /history and the phone before retrying.",
        "job-storage-unavailable": "The request's job folder could not be prepared. Check available disk space and folder access before retrying.",
        "completion-unverified": "The queued worker stopped without a matching verified completion. Inspect /history and the phone before retrying.",
        "worker-interrupted": "The queued worker stopped without a final receipt. Inspect /history and the phone before retrying.",
    }
    code = entry.get("failure_code")
    message = messages.get(
        code if isinstance(code, str) else "", "Inspect /history and the phone before retrying."
    )
    enqueue(
        DATA_ROOT,
        "queue-attention:" + entry["id"],
        "Queue paused. "
        + message
        + " Use /queue to review and dismiss the failed request when it is safe to continue.",
    )


def step():
    """Claim before spawn; after a crash ambiguous work is paused, never replayed."""
    from iphone_detector.discord_bot import command
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config
    from iphone_detector.job_history import read
    from iphone_detector import remote_tunnel

    with guard():
        values = entries()
        for entry in values:
            if entry["state"] != "running":
                continue
            if alive(entry):
                return True
            job = DATA_ROOT / "automation-runs" / ("queue-" + entry["id"])
            result = read(job / "result.json")
            expected = len(entry["selection"]["accounts"])
            from iphone_detector.operations import selection_matches

            original = read(job / "selection.json") or read(job / "backend-job.json")
            verified = (
                result.get("ok") is True
                and type(result.get("completed_accounts")) is int
                and result["completed_accounts"] == expected
                and not result.get("prepared_only")
                and selection_matches(original, entry["selection"])
            )
            entry["state"] = "completed" if verified else "failed" if result else "interrupted"
            if not verified:
                entry["failure_code"] = "completion-unverified" if result else "worker-interrupted"
            entry["finished_at"] = time.time()
            write_json(ROOT / (entry["id"] + ".json"), entry)
        if any(entry["state"] in ("failed", "interrupted") for entry in values):
            for entry in values:
                if entry["state"] in ("failed", "interrupted"):
                    report_failure(entry)
            return False
        entry = next((item for item in values if item["state"] == "queued"), None)
        if entry is None:
            return False
        lock = RunLock()
        try:
            if not lock.acquire():
                return True
        finally:
            lock.close()
        chosen = [item["account"] - 1 for item in entry["selection"]["accounts"]]
        failure_code = "selection-changed"
        try:
            request = make_job(load_config() or {}, "Network", accounts=chosen)
            if any(
                request.get(key) != entry["selection"].get(key) for key in ("serial", "accounts")
            ):
                raise ValueError("The account, phone or IPA changed after queuing.")
            from iphone_detector.connection_router import route_ready

            try:
                ready = route_ready(refresh=entry["mode"] == "refresh")
            except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
                LOG.exception("Queue connectivity check failed; request remains waiting.")
                ready = False
            if not ready:
                if entry.get("waiting_reason") != "connection":
                    entry["waiting_reason"] = "connection"
                    write_json(ROOT / (entry["id"] + ".json"), entry)
                try:
                    remote_tunnel.ensure_started()
                except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
                    LOG.exception("Could not start the remote tunnel; request remains waiting.")
                return True
            job = DATA_ROOT / "automation-runs" / ("queue-" + entry["id"])
            failure_code = "job-storage-unavailable"
            job.mkdir(parents=True, exist_ok=False)
            write_json(job / "selection.json", entry["selection"])
            entry["state"] = "running"
            entry.pop("waiting_reason", None)
            write_json(ROOT / (entry["id"] + ".json"), entry)
            args = [
                "--worker",
                "discord-refresh" if entry["mode"] == "refresh" else "discord-run",
                "--job-dir",
                str(job),
            ]
            if entry["mode"] != "refresh":
                for number in chosen:
                    args += ["--account", str(number + 1)]
            failure_code = "worker-launch-failed"
            process = subprocess.Popen(
                command(args),
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
            )
            failure_code = "worker-identity-unavailable"
            entry.update(pid=process.pid, process_created=psutil.Process(process.pid).create_time())
            write_json(ROOT / (entry["id"] + ".json"), entry)
        except OSError, ValueError, psutil.Error:
            entry["state"] = "failed"
            entry["failure_code"] = failure_code
            entry["finished_at"] = time.time()
            LOG.exception("Queued request paused (%s); no automatic replay.", failure_code)
            write_json(ROOT / (entry["id"] + ".json"), entry)
            report_failure(entry)
            return False
        return True


def ensure_started():
    if not pending():
        return
    from iphone_detector.job_history import read

    if alive(read(DATA_ROOT / "queue-runner.json")):
        return
    from iphone_detector.discord_bot import command

    subprocess.Popen(
        command(["--queue-runner"]),
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
    )


def run():
    lock = RunLock()
    lock.name = "Local\\iPhoneIPACommunityQueueRunner"
    if not lock.acquire():
        return
    try:
        from iphone_detector.activation_health import ready

        ready("queue")
        write_json(
            DATA_ROOT / "queue-runner.json",
            {"pid": os.getpid(), "process_created": psutil.Process().create_time()},
        )
        while True:
            try:
                if not step():
                    break
            except QueueBusy:
                pass
            time.sleep(2)
    finally:
        (DATA_ROOT / "queue-runner.json").unlink(missing_ok=True)
        lock.close()


def summary():
    values = entries()
    waiting = sum(entry["state"] == "queued" for entry in values)
    running = sum(entry["state"] == "running" for entry in values)
    paused = any(entry["state"] in ("failed", "interrupted") for entry in values)
    return f"Queue: {waiting} waiting, {running} running." + (
        " Paused: inspect and dismiss failed requests to continue." if paused else ""
    )
