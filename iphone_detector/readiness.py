"""Credential-free setup findings shared by desktop and private Discord controls."""

import shutil
import time

from iphone_detector.paths import ASSET_ROOT, DATA_ROOT


def inspect():
    from iphone_detector import auto_refresh, job_queue, remote_tunnel
    from iphone_detector.config import load_config
    from iphone_detector.settings import get_workflow

    checks = []

    def add(code, status, title, detail, action=""):
        checks.append(dict(code=code, status=status, title=title, detail=detail, action=action))

    try:
        config = load_config() or {}
        accounts = get_workflow(config)
        device = config.get("device", {})
        if not isinstance(device, dict):
            raise ValueError("Invalid device settings")
        add("configuration", "ok", "Settings", "Saved settings are readable.")
    except OSError, ValueError, TypeError:
        add(
            "configuration",
            "error",
            "Settings",
            "Saved settings need repair.",
            "Restore a settings backup or use local settings repair.",
        )
        config, accounts, device = {}, [], {}
    serial = device.get("serial")
    selected = isinstance(serial, str) and bool(serial.strip())
    add(
        "device",
        "ok" if selected else "error",
        "iPhone selection",
        "An iPhone is selected; live connectivity has not been tested."
        if selected
        else "No iPhone is selected.",
        "" if selected else "Connect by USB, unlock, approve Trust and select the phone.",
    )
    add(
        "accounts",
        "ok" if accounts else "error",
        "Apple accounts",
        "%d account assignments saved." % len(accounts),
        "" if accounts else "Choose an IPA folder, then add an Apple account and assign its app.",
    )
    from pathlib import Path

    folder = config.get("automation", {}).get("ipa_directory")
    try:
        folder = Path(folder) if isinstance(folder, str) and folder else None
        missing = (
            sum(not (folder / (a["ipa"] + ".ipa")).is_file() for a in accounts)
            if folder and folder.is_dir()
            else len(accounts)
        )
        exists = folder is not None and folder.is_absolute() and folder.is_dir()
    except OSError, ValueError:
        exists, missing = False, len(accounts)
    add(
        "files",
        "ok" if exists and accounts and not missing else "error",
        "IPA files",
        "All assigned files exist; archive integrity is checked before installation."
        if exists and accounts and not missing
        else "Choose an accessible IPA folder containing every assigned file.",
        ""
        if exists and accounts and not missing
        else "Copy completed IPA files into the folder, select them and save settings.",
    )
    backend = (ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe").is_file()
    add(
        "backend",
        "ok" if backend else "error",
        "Installation engine",
        "Native backend is available." if backend else "The installation engine is missing.",
        "" if backend else "Reinstall from a complete verified distribution.",
    )
    try:
        free = shutil.disk_usage(DATA_ROOT.anchor).free
        add(
            "storage",
            "ok" if free >= 1024**3 else "warning",
            "Free space",
            "At least 1 GiB is available." if free >= 1024**3 else "Less than 1 GiB is available.",
            "" if free >= 1024**3 else "Free space before installing or preparing an update.",
        )
    except OSError:
        add(
            "storage",
            "warning",
            "Free space",
            "Available disk space could not be checked.",
            "Check the drive containing application settings.",
        )
    try:
        settings, success, state = auto_refresh.schedule_snapshot()
        pending = bool(state.get("pending_job")) and success.get("at", 0) <= state.get(
            "started_at", 0
        )
        enabled = settings.get("enabled") is True
        due = enabled and time.time() >= auto_refresh.due_time(settings, success)
        add(
            "renewal",
            "warning" if pending or due else "ok" if enabled else "info",
            "Automatic renewal",
            "An unresolved renewal needs review."
            if pending
            else "Renewal is due and will wait for an available connection."
            if due
            else "Six-day renewal is enabled."
            if enabled
            else "Automatic renewal is disabled.",
            "Inspect job history before retrying."
            if pending
            else "Keep the PC on and signed in; the phone must be reachable."
            if enabled
            else "Enable automatic renewal after a successful installation.",
        )
    except OSError, ValueError, TypeError:
        add(
            "renewal",
            "error",
            "Automatic renewal",
            "Renewal state needs repair; automatic work is paused.",
            "Inspect history and restore or repair the saved renewal state locally.",
        )
    try:
        entries = job_queue.entries()
        blocked = any(item["state"] in ("failed", "interrupted") for item in entries)
        add(
            "queue",
            "warning" if blocked else "ok",
            "Queue",
            "An unresolved request has paused the queue."
            if blocked
            else "No unresolved queue failure.",
            "Inspect the failed run, then dismiss it explicitly to continue." if blocked else "",
        )
    except OSError, ValueError, TypeError:
        add(
            "queue",
            "error",
            "Queue",
            "Saved queue records need repair.",
            "Inspect the local queue folder; preserve unresolved job evidence.",
        )
    try:
        ready = remote_tunnel.state().get("state") == "ready"
        add(
            "remote",
            "ok" if ready else "info",
            "Remote tunnel",
            "A recent remote-ready heartbeat is present."
            if ready
            else "No remote-ready heartbeat; USB or local Wi-Fi can still work.",
            "" if ready else "For remote use, establish the tunnel on home Wi-Fi before leaving.",
        )
    except OSError, ValueError, TypeError:
        add(
            "remote",
            "warning",
            "Remote tunnel",
            "Remote status is unavailable.",
            "Check local remote setup; a live connection has not been verified.",
        )
    return {
        "schema_version": 1,
        "checks": checks,
        "needs_attention": any(c["status"] == "error" for c in checks),
        "device_accessed": False,
    }


def format_report(value):
    lines = ["Setup and recovery checklist (no device changes)"]
    for row in value["checks"]:
        lines.append(
            "%s: %s — %s%s"
            % (
                row["status"].upper(),
                row["title"],
                row["detail"],
                " " + row["action"] if row["action"] else "",
            )
        )
    return "\n\n".join(lines)
