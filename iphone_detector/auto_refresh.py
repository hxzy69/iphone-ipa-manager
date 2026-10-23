"""Six-day renewal, checked by an ordinary-user Windows scheduled task."""

from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import time

from iphone_detector.paths import DATA_ROOT

INTERVAL = 6 * 24 * 60 * 60
TASK_NAME = "iPhone IPA Community Automatic Refresh"
CONFIG = DATA_ROOT / "auto-refresh.json"
STATE = DATA_ROOT / "auto-refresh-state.json"
SUCCESS = DATA_ROOT / "renewal-last-success.json"
LOG = logging.getLogger("iphone-detector.auto-refresh")


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except OSError, ValueError:
        return {}


def verified_success(result, expected=None):
    if expected is None:
        from iphone_detector.settings import get_workflow

        expected = len(get_workflow())
    return (
        type(expected) is int
        and expected > 0
        and result.get("ok") is True
        and type(result.get("completed_accounts")) is int
        and result["completed_accounts"] == expected
        and not result.get("prepared_only")
    )


def schedule_snapshot():
    """Missing initial files are allowed; existing invalid state must never reset a timer."""
    values = []
    for path in (CONFIG, SUCCESS, STATE):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            value = {}
        except (OSError, ValueError) as exc:
            raise ValueError("Renewal state needs local repair: " + path.name) from exc
        if not isinstance(value, dict):
            raise ValueError("Renewal state needs local repair: " + path.name)
        for key in ("anchor", "at", "started_at", "completed_at"):
            if key in value and (
                type(value[key]) not in (int, float)
                or not math.isfinite(value[key])
                or value[key] < 0
            ):
                raise ValueError("Invalid renewal timestamp: " + path.name)
        values.append(value)
    config, success, state = values
    if (
        "enabled" in config
        and type(config["enabled"]) is not bool
        or config.get("enabled")
        and "anchor" not in config
        or success
        and "at" not in success
        or "pending_job" in state
        and (
            not isinstance(state["pending_job"], str)
            or not state["pending_job"]
            or "started_at" not in state
        )
    ):
        raise ValueError("Renewal state needs local repair; automatic installation is paused.")
    return config, success, state


def record_success(job, result):
    if verified_success(result):
        from iphone_detector.remote_tunnel import atomic

        try:
            at = (Path(job) / "result.json").stat().st_mtime
        except OSError:
            at = time.time()
        atomic(SUCCESS, {"at": at, "job": str(job)})
    elif result.get("ok") is True and not result.get("prepared_only"):
        from iphone_detector.config import load_config
        from iphone_detector.operations import account_status
        from iphone_detector.settings import get_workflow
        from iphone_detector.storage import write_json

        config = load_config() or {}
        outcomes = account_status(DATA_ROOT, config)
        if len(outcomes) != len(get_workflow(config)) or not outcomes:
            return
        _, success, state = schedule_snapshot()
        threshold = max(
            success.get("at", 0), state.get("started_at", 0) if state.get("pending_job") else 0
        )
        earliest = min(receipt["at"] for receipt in outcomes.values())
        if threshold < earliest <= time.time() < earliest + INTERVAL:
            write_json(
                SUCCESS, {"at": earliest, "job": str(job), "combined_verified_accounts": True}
            )


def recover_completed(state):
    """Recover a receipt written before the scheduler/controller exited unexpectedly.

    Failed and partial jobs remain paused; this never resubmits device work.
    """
    if not state.get("pending_job"):
        return False
    job = Path(state["pending_job"])
    receipt = job / "result.json"
    result = read(receipt)
    if not verified_success(result):
        return False
    try:
        completed_at = receipt.stat().st_mtime
    except OSError:
        return False
    if completed_at < state.get("started_at", 0):
        return False
    from iphone_detector.remote_tunnel import atomic

    # Preserve the real completion time rather than postponing renewal to reboot time.
    if read(SUCCESS).get("at", 0) < completed_at:
        atomic(SUCCESS, {"at": completed_at, "job": str(job)})
    atomic(STATE, {"completed_job": str(job), "completed_at": completed_at, "recovered": True})
    from iphone_detector.discord_outbox import enqueue

    enqueue(
        DATA_ROOT,
        "automatic-complete:" + str(job),
        "Recovered a verified automatic renewal completion after interruption. All configured accounts completed; no installation was replayed. Use /status for the next due date.",
    )
    return True


def decision(config, success, state, now, ready, busy):
    if not config.get("enabled"):
        return "disabled"
    last = float(success.get("at", config["anchor"]))
    if state.get("pending_job") and last <= state.get("started_at", 0):
        return "attention"
    if now < last + INTERVAL:
        return "scheduled"
    if busy:
        return "busy"
    return "due" if ready else "waiting"


def describe():
    try:
        config, success, state = schedule_snapshot()
    except ValueError:
        return "Automatic refresh needs attention: renewal state needs local repair; no automatic installation will start."
    if not config.get("enabled"):
        return "Automatic refresh disabled."
    if state.get("pending_job") and success.get("at", 0) <= state.get("started_at", 0):
        try:
            heartbeat = Path(state["pending_job"]) / "remote-heartbeat"
            if not state.get("notified") and time.time() - heartbeat.stat().st_mtime < 15:
                return "Automatic refresh is running."
        except OSError:
            pass
        return "Automatic refresh needs attention. Inspect the last job; a successful /refresh or /sideload resumes the schedule."
    due = float(success.get("at", config["anchor"])) + INTERVAL
    if time.time() >= due:
        return "Automatic refresh due; waiting for an available tunnel and no active installation."
    return (
        "Automatic refresh due "
        + datetime.fromtimestamp(due).astimezone().strftime("%d %b %H:%M %Z")
        + " (every 6 days)."
    )


def tick():
    from iphone_detector import remote_tunnel
    from iphone_detector.run_lock import RunLock
    from iphone_detector.discord_bot import command

    singleton = RunLock()
    singleton.name = "Local\\iPhoneIPAManagerAutoRefresh"
    if not singleton.acquire():
        return
    try:
        try:
            config, success, state = schedule_snapshot()
        except ValueError:
            LOG.exception("Automatic refresh paused because saved renewal state is invalid.")
            remote_tunnel.atomic(
                DATA_ROOT / "auto-refresh-check.json",
                {"checked_at": time.time(), "status": "state-needs-repair"},
            )
            from iphone_detector.discord_outbox import enqueue

            enqueue(
                DATA_ROOT,
                "renewal-state-needs-repair",
                "Automatic renewal paused: saved renewal state needs local repair. No installation was started. Use /check and inspect the local automatic refresh log.",
            )
            return
        if not config.get("enabled"):
            return
        guard = RunLock()
        available = guard.acquire()
        guard.close()
        if available and recover_completed(state):
            success, state = read(SUCCESS), read(STATE)
        action = decision(
            config,
            success,
            state,
            time.time(),
            remote_tunnel.enabled() and remote_tunnel.state()["state"] == "ready",
            not available,
        )
        remote_tunnel.atomic(
            DATA_ROOT / "auto-refresh-check.json",
            {
                "checked_at": time.time(),
                "status": action,
                "next_due": float(success.get("at", config["anchor"])) + INTERVAL,
            },
        )
        if action != "due":
            # Never replay a job left unresolved by a crash or interrupted task.
            if action == "attention":
                attention(state)
            elif action == "waiting":
                remote_tunnel.ensure_started()
            return
        job = (
            DATA_ROOT
            / "automation-runs"
            / ("automatic-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        )
        job.mkdir(parents=True)
        state = {"pending_job": str(job), "started_at": time.time()}
        remote_tunnel.atomic(STATE, state)
        try:
            process = subprocess.Popen(
                command(
                    [
                        "--worker",
                        "discord-refresh",
                        "--job-dir",
                        str(job),
                        "--parent-pid",
                        str(os.getpid()),
                    ]
                ),
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
            )
        except OSError:
            # The worker never existed, so retrying preflight later cannot replay device work.
            remote_tunnel.atomic(
                STATE, {"deferred_job": str(job), "reason": "worker-launch-failed"}
            )
            LOG.exception("Could not launch renewal worker; deferred to a later check.")
            return
        exit_code = process.wait()
        result = read(job / "result.json")
        if exit_code == 0 and verified_success(result):
            record_success(job, result)
            remote_tunnel.atomic(STATE, {"completed_job": str(job), "completed_at": time.time()})
            from iphone_detector.discord_outbox import enqueue

            enqueue(
                DATA_ROOT,
                "automatic-complete:" + str(job),
                "Automatic six-day renewal completed for all configured accounts. Use /history for the outcome and /status for the next due date.",
            )
        elif result.get("error_code") == "CONFIG_UNREADABLE":
            attention(state)
        elif (
            not (job / "remote-request.json").exists()
            and not (job / "backend-events.jsonl").exists()
        ):
            # Preflight rejected this run before submitting any phone/account work.
            remote_tunnel.atomic(STATE, {"deferred_job": str(job)})
        else:
            attention(state)
    finally:
        singleton.close()


def attention(state):
    if not state.get("notified"):
        from iphone_detector.remote_tunnel import atomic
        from iphone_detector.notify import notify
        from iphone_detector.discord_outbox import enqueue

        message = "A renewal was interrupted or failed. Inspect /history before using /refresh or /sideload. Automatic retries are paused."
        queued = enqueue(DATA_ROOT, "automatic-attention:" + str(state.get("pending_job")), message)
        try:
            notify("iPhone automatic refresh needs attention", message)
        except Exception:
            LOG.exception("Windows renewal notification failed; Discord report queued: %s", queued)
            if not queued:
                return
        atomic(STATE, dict(state, notified=True))


def configure(enabled):
    """Install a hidden logon + 15-minute due check; no elevation or password."""
    if enabled:
        from iphone_detector.settings import get_workflow

        if not get_workflow():
            raise ValueError("Add and install an Apple account before enabling automatic renewal.")
    import pythoncom
    import win32com.client
    from iphone_detector.bridge_service import current_sid
    from iphone_detector.discord_bot import command
    from iphone_detector.remote_tunnel import atomic

    config = schedule_snapshot()[0] if enabled else read(CONFIG)
    pythoncom.CoInitialize()
    scheduler = folder = task = trigger = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        if enabled:
            if not read(SUCCESS):
                previous = []
                for receipt in (DATA_ROOT / "automation-runs").glob("*/result.json"):
                    result = read(receipt)
                    if verified_success(result):
                        previous.append((receipt.stat().st_mtime, str(receipt.parent)))
                if previous:
                    at, job = max(previous)
                    atomic(SUCCESS, {"at": at, "job": job})
            owner = current_sid()
            task = scheduler.NewTask(0)
            task.RegistrationInfo.Description = "Check every 15 minutes; renew iPhone apps six days after the last successful renewal."
            task.Principal.UserId, task.Principal.LogonType, task.Principal.RunLevel = owner, 3, 0
            trigger = task.Triggers.Create(9)
            trigger.UserId, trigger.Delay = owner, "PT30S"
            trigger = task.Triggers.Create(2)
            trigger.StartBoundary = datetime.now().astimezone().isoformat(timespec="seconds")
            trigger.DaysInterval = 1
            trigger.Repetition.Interval, trigger.Repetition.Duration = "PT15M", "P1D"
            action = task.Actions.Create(0)
            args = command(["--auto-refresh"])
            action.Path, action.Arguments = args[0], subprocess.list2cmdline(args[1:])
            action.WorkingDirectory = str(Path(args[0]).parent)
            task.Settings.StartWhenAvailable = True
            task.Settings.Hidden = True
            task.Settings.DisallowStartIfOnBatteries = False
            task.Settings.StopIfGoingOnBatteries = False
            task.Settings.ExecutionTimeLimit = "PT1H"
            task.Settings.MultipleInstances = 2
            folder.RegisterTaskDefinition(TASK_NAME, task, 6, owner, None, 3)
        else:
            for saved in folder.GetTasks(0):
                if saved.Name == TASK_NAME:
                    folder.DeleteTask(TASK_NAME, 0)
                    break
        atomic(
            CONFIG, dict(config, enabled=bool(enabled), anchor=config.get("anchor", time.time()))
        )
    finally:
        action = trigger = task = folder = scheduler = None
        pythoncom.CoUninitialize()


def run():
    from iphone_detector.logger import setup_logging

    setup_logging(log_file=str(DATA_ROOT / "auto-refresh.log"))
    tick()
