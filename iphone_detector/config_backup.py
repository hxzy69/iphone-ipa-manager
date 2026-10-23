"""Export preferences and assignments; never transport credentials or pairing."""

import copy
import json
from pathlib import Path

from iphone_detector.config import load_config, save_config
from iphone_detector.settings import get_workflow
from iphone_detector.run_lock import RunLock

PREFERENCES = {
    "install_timeout",
    "notify_on_finish",
    "wifi_archive_transfer",
    "close_gui_after_success",
}


def validate(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported configuration backup.")
    settings = value.get("preferences", {})
    if not isinstance(settings, dict) or set(settings) - PREFERENCES:
        raise ValueError("Backup contains unsupported preferences.")
    for key, item in settings.items():
        if key == "install_timeout":
            if type(item) is not int or not 60 <= item <= 7200:
                raise ValueError("Invalid timeout in backup.")
        elif type(item) is not bool:
            raise ValueError("Invalid preference in backup.")
    accounts = value.get("accounts")
    workflow = get_workflow({"automation": {"accounts": accounts}})
    cleaned = [{"email": entry["email"], "ipa": entry["ipa"]} for entry in workflow]
    # Reject hidden fields instead of quietly accepting secret-bearing payloads.
    if not isinstance(accounts, list) or accounts != cleaned:
        raise ValueError("Backup account fields are invalid or contain unsupported data.")
    if set(value) != {"schema_version", "preferences", "accounts"}:
        raise ValueError("Backup contains unsupported fields.")
    return {"schema_version": 1, "preferences": dict(settings), "accounts": cleaned}


def export():
    config = load_config() or {}
    automation = config.get("automation", {})
    return validate(
        {
            "schema_version": 1,
            "accounts": [{"email": a["email"], "ipa": a["ipa"]} for a in get_workflow(config)],
            "preferences": {key: automation[key] for key in PREFERENCES if key in automation},
        }
    )


def preview(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError("Backup is too large.")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def restore(value):
    from iphone_detector.ipa_assignments import mark_pending
    from iphone_detector import job_queue

    value = validate(value)
    guard = RunLock()
    if not guard.acquire():
        raise ValueError("Wait for active jobs or account changes before restoring.")
    try:
        if job_queue.pending():
            raise ValueError("Cancel queued requests before restoring account assignments.")
        config = copy.deepcopy(load_config() or {})
        from iphone_detector.paths import DATA_ROOT
        from iphone_detector.storage import write_json
        import time

        write_json(DATA_ROOT / "backups" / f"before-restore-{time.time_ns()}.json", export())
        automation = config.setdefault("automation", {})
        automation.update(value["preferences"])
        automation["accounts"] = value["accounts"]
        automation["pending_ipa_installs"] = {}
        for index, account in enumerate(value["accounts"]):
            mark_pending(config, index, account["ipa"])
        save_config(config)
        return len(value["accounts"])
    finally:
        guard.close()
