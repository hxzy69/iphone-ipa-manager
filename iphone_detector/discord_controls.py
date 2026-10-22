"""Shared business rules for private Discord management commands."""

import copy
from pathlib import Path

from iphone_detector.config import load_config, save_config
from iphone_detector.settings import get_workflow
from iphone_detector.run_lock import RunLock
from iphone_detector.ipa_assignments import mark_pending


def settings_summary(config):
    value = config.get("automation", {})
    return {
        "folder": str(
            Path(value.get("ipa_directory", str(Path.home() / "Downloads/ipas"))).expanduser()
        ),
        "timeout_minutes": int(value.get("install_timeout", 900)) // 60,
        "notifications": bool(value.get("notify_on_finish", True)),
    }


def change_settings(folder=None, timeout=None, notifications=None):
    guard = RunLock()
    if not guard.acquire():
        raise ValueError(
            "A run or settings change is active. Use /status and try again after it finishes."
        )
    try:
        config = copy.deepcopy(load_config() or {})
        automation = config.setdefault("automation", {})
        if folder is not None:
            path = Path(folder).expanduser()
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("Choose an existing absolute Windows folder path.")
            workflow = get_workflow(config)
            missing = [
                str(i + 1)
                for i, account in enumerate(workflow)
                if not (path / (account["ipa"] + ".ipa")).is_file()
            ]
            if missing:
                raise ValueError(
                    "Copy the saved IPA files for accounts %s into that folder first. The folder was not changed."
                    % ", ".join(missing)
                )
            if path.resolve() != Path(settings_summary(config)["folder"]).resolve():
                for index, account in enumerate(workflow):
                    mark_pending(config, index, account["ipa"])
            automation["ipa_directory"] = str(path.resolve())
        if timeout is not None:
            if type(timeout) is not int or not 1 <= timeout <= 120:
                raise ValueError("Timeout must be between 1 and 120 minutes per account.")
            automation["install_timeout"] = timeout * 60
        if notifications is not None:
            if type(notifications) is not bool:
                raise ValueError("Notifications must be enabled or disabled.")
            automation["notify_on_finish"] = notifications
        save_config(config)
        return settings_summary(config)
    finally:
        guard.close()


def setup_check():
    from iphone_detector import remote_tunnel, auto_refresh
    from iphone_detector.paths import ASSET_ROOT
    from iphone_detector.bridge_service import installed

    config = load_config() or {}
    folder = Path(settings_summary(config)["folder"])
    workflow = get_workflow(config)
    missing = [
        str(index + 1)
        for index, account in enumerate(workflow)
        if not (folder / (account["ipa"] + ".ipa")).is_file()
    ]
    return {
        "paired": bool(config.get("device", {}).get("serial")),
        "missing_accounts": missing,
        "folder_exists": folder.is_dir(),
        "backend_exists": (ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe").is_file(),
        "wifi_service": installed(),
        "tunnel": remote_tunnel.describe(),
        "schedule": auto_refresh.describe(),
        "note": "Read-only checks; Apple credentials and a new installation were not tested.",
    }
