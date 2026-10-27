"""Six-day renewal, checked by an ordinary-user Windows scheduled task."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import uuid
from iphone_detector.storage import finite_number

from iphone_detector.paths import DATA_ROOT
from iphone_detector.com_runtime import initialize, uninitialize

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
        for key in ("anchor", "at", "started_at", "completed_at", "next_due"):
            if key in value and (
                not finite_number(value[key])
                or value[key] < 0
                or (key != "next_due" and value[key] > time.time() + 300)
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
    selection = success.get("selection")
    if selection is not None and not isinstance(selection, dict):
        raise ValueError("Renewal state needs local repair: " + SUCCESS.name)
    if success and selection is None:
        # Older versions saved only the job path. Recover its identity rather than
        # allowing a timer from another phone or assignment to postpone renewal.
        from iphone_detector.operations import verified_job

        saved_job = success.get("job")
        job = Path(saved_job) if isinstance(saved_job, str) and saved_job else None
        if job is not None and verified_job(job, read(job / "result.json")):
            selection = read(job / "selection.json") or read(job / "backend-job.json")
            success = dict(success, selection=selection)
        else:
            success = {}
            config = dict(config, anchor=min(config.get("anchor", 0), time.time() - INTERVAL))
    if selection is not None:
        from iphone_detector.config import load_config
        from iphone_detector.background import make_job
        from iphone_detector.operations import selection_matches

        try:
            current = make_job(load_config() or {}, "Network", verify_content=False)
            matches = selection_matches(selection, current, content=False)
        except OSError, ValueError, TypeError:
            matches = False
        if not matches:
            # A new phone/assignment cannot inherit the previous selection's timer.
            success = {}
            config = dict(config, anchor=min(config.get("anchor", 0), time.time() - INTERVAL))
    return config, success, state


def due_time(config, success):
    return min(
        float(success.get("at", config["anchor"])) + INTERVAL,
        float(success.get("next_due", float("inf"))),
    )


def success_receipt(job, at, selection):
    from iphone_detector.operations import account_status
    from iphone_detector.config import load_config

    value = {"at": at, "job": str(job), "selection": selection}
    outcomes = account_status(DATA_ROOT, load_config() or {})
    expiries = [
        r["profile_expires_at"]
        for r in outcomes.values()
        if type(r.get("profile_expires_at")) in (int, float)
    ]
    if expiries:
        value["next_due"] = max(0, min(at + INTERVAL, min(expiries) - 24 * 60 * 60))
    return value


def seed_success():
    """Find a matching completed run without inheriting a stale or future timer."""
    from iphone_detector.operations import jobs, verified_job

    newest = None
    now = time.time()
    for job in jobs(DATA_ROOT, limit=None):
        try:
            at = (job / "result.json").stat().st_mtime
            if not 0 <= at <= now or (newest is not None and at <= newest[0]):
                continue
            if not verified_job(job, read(job / "result.json")):
                continue
            selection = read(job / "selection.json") or read(job / "backend-job.json")
            newest = (at, job, selection)
        except OSError:
            continue
    if newest is None:
        return None
    at, job, selection = newest
    return success_receipt(job, at, selection)


def record_success(job, result):
    from iphone_detector.operations import verified_job

    if verified_job(Path(job), result):
        from iphone_detector.remote_tunnel import atomic

        try:
            at = (Path(job) / "result.json").stat().st_mtime
        except OSError:
            at = time.time()
        selection = read(Path(job) / "selection.json") or read(Path(job) / "backend-job.json")
        atomic(SUCCESS, success_receipt(job, at, selection))
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
            from iphone_detector.background import make_job

            value = success_receipt(job, earliest, make_job(config, "Network"))
            value["combined_verified_accounts"] = True
            write_json(SUCCESS, value)


def recover_completed(state):
    """Recover a receipt written before the scheduler/controller exited unexpectedly.

    Failed and partial jobs remain paused; this never resubmits device work.
    """
    if not state.get("pending_job"):
        return False
    job = Path(state["pending_job"])
    receipt = job / "result.json"
    result = read(receipt)
    from iphone_detector.operations import verified_job

    if not verified_job(job, result):
        return False
    try:
        completed_at = receipt.stat().st_mtime
    except OSError:
        return False
    if completed_at < state.get("started_at", 0) or completed_at > time.time() + 300:
        return False
    from iphone_detector.remote_tunnel import atomic

    # Preserve the real completion time rather than postponing renewal to reboot time.
    if read(SUCCESS).get("at", 0) < completed_at:
        selection = read(job / "selection.json") or read(job / "backend-job.json")
        atomic(SUCCESS, success_receipt(job, completed_at, selection))
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
    if now < due_time(config, success):
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
    due = due_time(config, success)
    if time.time() >= due:
        return "Automatic refresh due; waiting for a verified phone connection and no active installation."
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
            check_path = DATA_ROOT / "auto-refresh-check.json"
            previous = read(check_path)
            incident = (
                previous.get("incident") if previous.get("status") == "state-needs-repair" else None
            )
            if not isinstance(incident, str) or not incident:
                incident = uuid.uuid4().hex
            remote_tunnel.atomic(
                check_path,
                {"checked_at": time.time(), "status": "state-needs-repair", "incident": incident},
            )
            from iphone_detector.discord_outbox import enqueue

            enqueue(
                DATA_ROOT,
                "renewal-state-needs-repair:" + incident,
                "Automatic renewal paused: saved renewal state needs local repair. No installation was started. Use /check and inspect the local automatic refresh log.",
            )
            return
        if not config.get("enabled"):
            # Clear any previous repair incident, even while renewal is disabled,
            # so a later independent failure can produce a new notification.
            check_path = DATA_ROOT / "auto-refresh-check.json"
            if read(check_path).get("status") == "state-needs-repair":
                remote_tunnel.atomic(check_path, {"checked_at": time.time(), "status": "disabled"})
            return
        guard = RunLock()
        available = guard.acquire()
        guard.close()
        if available and recover_completed(state):
            success, state = read(SUCCESS), read(STATE)
        from iphone_detector.connection_router import route_ready

        action = decision(config, success, state, time.time(), False, not available)
        if action == "waiting":
            try:
                ready = route_ready(refresh=True)
            except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
                LOG.exception("Renewal connectivity check failed; deferred to a later check.")
                ready = False
            action = decision(config, success, state, time.time(), ready, not available)
        remote_tunnel.atomic(
            DATA_ROOT / "auto-refresh-check.json",
            {
                "checked_at": time.time(),
                "status": action,
                "next_due": due_time(config, success),
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
        from iphone_detector.operations import verified_job

        if exit_code == 0 and verified_job(job, result):
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
            and not (job / "native-dispatch.json").exists()
            and not (job / "native-input.json").exists()
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
    import win32com.client
    from iphone_detector.bridge_service import current_sid
    from iphone_detector.discord_bot import command
    from iphone_detector.remote_tunnel import atomic

    config = schedule_snapshot()[0] if enabled else read(CONFIG)
    com_owned = initialize()
    scheduler = folder = task = trigger = action = None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        if enabled:
            if not read(SUCCESS):
                previous = seed_success()
                if previous:
                    atomic(SUCCESS, previous)
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
            # The worker applies per-account signing/transfer deadlines. A fixed
            # one-hour supervisor limit can kill a valid 25-account workflow.
            task.Settings.ExecutionTimeLimit = "PT0S"
            task.Settings.MultipleInstances = 2
            folder.RegisterTaskDefinition(TASK_NAME, task, 6, owner, None, 3)
        else:
            for saved in folder.GetTasks(1):
                if saved.Name == TASK_NAME:
                    folder.DeleteTask(TASK_NAME, 0)
                    break
        atomic(
            CONFIG, dict(config, enabled=bool(enabled), anchor=config.get("anchor", time.time()))
        )
    finally:
        action = trigger = task = folder = scheduler = None
        uninitialize(com_owned)


def run():
    from iphone_detector.logger import setup_logging

    setup_logging(log_file=str(DATA_ROOT / "auto-refresh.log"))
    tick()
