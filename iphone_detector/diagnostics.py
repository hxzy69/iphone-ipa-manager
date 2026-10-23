"""Small, read-only support snapshots built from an explicit field allowlist."""

import platform
import time

from iphone_detector import __version__
from iphone_detector.paths import DATA_ROOT, FROZEN


def report():
    from iphone_detector import auto_refresh, remote_tunnel
    from iphone_detector.config import load_config
    from iphone_detector.settings import get_workflow
    from iphone_detector.worker_registry import active_jobs

    result = {
        "schema_version": 1,
        "version": __version__,
        "standalone": FROZEN,
        "python": platform.python_version(),
        "system": platform.system(),
    }
    try:
        config = load_config() or {}
        result["configured_accounts"] = len(get_workflow(config))
        result["device_selected"] = bool(config.get("device", {}).get("serial"))
        result["configuration"] = "readable"
    except Exception:
        result["configuration"] = "unreadable"
    try:
        state = remote_tunnel.state().get("state")
        result["remote_tunnel"] = (
            state
            if state in ("ready", "installing", "waiting", "connecting", "offline")
            else "unknown"
        )
    except Exception:
        result["remote_tunnel"] = "unreadable"
    try:
        result["active_jobs"] = len(active_jobs(DATA_ROOT))
    except Exception:
        result["active_jobs"] = "unreadable"
    try:
        config, success, state = auto_refresh.schedule_snapshot()
        result["automatic_renewal"] = "enabled" if config.get("enabled") is True else "disabled"
        result["renewal_pending"] = bool(state.get("pending_job"))
        anchor = success.get("at", config.get("anchor"))
        result["renewal_overdue"] = (
            time.time() >= anchor + auto_refresh.INTERVAL if anchor is not None else None
        )
    except Exception:
        result["automatic_renewal"] = "unreadable"
    return result
