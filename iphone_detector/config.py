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


def validate_config(value):
    """Validate fields consumed by the app without discarding unknown preferences."""
    from iphone_detector.settings import get_workflow, ipa_stem

    if not isinstance(value, dict):
        raise ValueError("Settings must be a JSON object.")
    for section in ("device", "automation"):
        if not isinstance(value.get(section, {}), dict):
            raise ValueError(section.capitalize() + " settings must be an object.")
    device, automation = value.get("device", {}), value.get("automation", {})
    for key in ("serial", "name"):
        if key in device and not isinstance(device[key], str):
            raise ValueError("Invalid iPhone " + key + " setting.")
    get_workflow(value)
    if "install_timeout" in automation and (
        type(automation["install_timeout"]) is not int
        or not 60 <= automation["install_timeout"] <= 7200
    ):
        raise ValueError("Installation timeout must be between 60 and 7200 seconds.")
    for key in (
        "background_mode",
        "wifi_archive_transfer",
        "notify_on_finish",
        "close_gui_after_success",
    ):
        if key in automation and type(automation[key]) is not bool:
            raise ValueError("Invalid boolean preference: " + key + ".")
    for key in ("ipa_directory", "import_directory", "anisette_server"):
        if key in automation and (
            not isinstance(automation[key], str) or not automation[key].strip()
        ):
            raise ValueError("Invalid text preference: " + key + ".")
    pending = automation.get("pending_ipa_installs", {})
    if not isinstance(pending, dict):
        raise ValueError("Pending IPA assignments must be an object.")
    for number, stem in pending.items():
        if (
            not isinstance(number, str)
            or not number.isascii()
            or not number.isdecimal()
            or not 1 <= int(number) <= 25
        ):
            raise ValueError("Invalid pending account number.")
        if not isinstance(stem, str):
            raise ValueError("Invalid pending IPA filename.")
        ipa_stem(stem)


def load_config() -> Optional[Dict[str, Any]]:
    """Missing settings allow setup; unreadable existing settings must fail closed."""
    path = CONFIG_PATH
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        validate_config(result)
        return result
    except FileNotFoundError:
        return None
    except OSError, ValueError:
        LOG.error("Configuration is unreadable: %s", path)
        raise ConfigurationError(
            "Settings could not be read. Repair or restore "
            + str(path)
            + " before starting a run; existing settings were preserved."
        ) from None


def save_config(config: Dict[str, Any]) -> None:
    """Persist the config and stamp the pairing time."""
    validate_config(config)
    if config.get("device", {}).get("serial"):
        config["device"].setdefault("paired_at", datetime.now().isoformat(timespec="seconds"))
    try:
        from iphone_detector.storage import write_json

        write_json(CONFIG_PATH, config)
    except OSError:
        LOG.exception("Could not write %s", CONFIG_PATH)
        raise
