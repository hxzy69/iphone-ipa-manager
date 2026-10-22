"""Persistent per-user configuration; missing settings start an empty setup."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from iphone_detector.paths import DATA_ROOT

LOG = logging.getLogger("iphone-detector")

PROJECT_ROOT = DATA_ROOT
CONFIG_PATH = PROJECT_ROOT / "config.json"


class ConfigurationError(ValueError):
    pass


def load_config() -> Optional[Dict[str, Any]]:
    """Missing settings allow setup; unreadable existing settings must fail closed."""
    path = CONFIG_PATH
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("Configuration must be a JSON object")
        return result
    except OSError, ValueError:
        LOG.error("Configuration is unreadable: %s", path)
        raise ConfigurationError(
            "Settings could not be read. Repair or restore "
            + str(path)
            + " before starting a run; existing settings were preserved."
        ) from None


def save_config(config: Dict[str, Any]) -> None:
    """Persist the config and stamp the pairing time."""
    if config.get("device", {}).get("serial"):
        config["device"].setdefault("paired_at", datetime.now().isoformat(timespec="seconds"))
    try:
        from iphone_detector.storage import write_json

        write_json(CONFIG_PATH, config)
    except OSError:
        LOG.exception("Could not write %s", CONFIG_PATH)
        raise
