"""Contain unattended component failures without opening a crash dialog."""

import logging
import time
import uuid

from iphone_detector.paths import DATA_ROOT

LOG = logging.getLogger("iphone-detector.service")
COMPONENTS = {"queue", "renewal", "discord", "remote"}
LOG_FILES = {
    "queue": "queue.log",
    "renewal": "auto-refresh.log",
    "discord": "discord.log",
    "remote": "remote-tunnel.log",
}


def run(component, callback):
    if component not in COMPONENTS:
        raise ValueError("Unknown background component")
    from iphone_detector.logger import setup_logging
    from iphone_detector.storage import write_json
    from iphone_detector.job_history import read
    from iphone_detector.discord_outbox import enqueue

    status = DATA_ROOT / (component + "-service.json")
    try:
        setup_logging(log_file=LOG_FILES[component])
        code = callback()
        if code not in (None, 0):
            raise RuntimeError("Background component stopped unsuccessfully")
    except Exception:
        LOG.exception(
            "Background %s stopped; no automatic installation replay was requested.", component
        )
        previous = read(status)
        incident = previous.get("incident") if previous.get("state") == "failed" else None
        if not isinstance(incident, str) or not incident:
            incident = uuid.uuid4().hex
        try:
            write_json(
                status,
                {
                    "state": "failed",
                    "component": component,
                    "incident": incident,
                    "at": time.time(),
                },
            )
        except OSError:
            LOG.warning("Could not save background failure status.")
        try:
            enqueue(
                DATA_ROOT,
                "service-attention:" + component + ":" + incident,
                f"The {component} background service stopped. Inspect /diagnostics and the local {LOG_FILES[component]} before retrying. Existing job receipts and settings were preserved; no device work was automatically repeated.",
            )
        except Exception:
            LOG.warning("Could not queue the background failure notification.")
        return 1
    try:
        write_json(status, {"state": "stopped", "component": component, "at": time.time()})
    except OSError:
        LOG.warning("Could not save background completion status.")
    return 0
