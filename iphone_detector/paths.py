"""Resolve immutable application assets and private, per-user state."""

import os
import hashlib
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
ASSET_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def data_directory():
    """Share a state directory between source and packaged runs, outside the checkout."""
    override = os.environ.get("IPA_MANAGER_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("IPA_MANAGER_DATA_DIR must be an absolute folder path.")
        return path.resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "iPhoneIPAManagerCommunity"


DATA_ROOT = data_directory()


def profile_lock_name(component):
    identity = str(DATA_ROOT.resolve()).casefold().encode("utf-8")
    return "Local\\iPhoneIPACommunity" + component + "-" + hashlib.sha256(identity).hexdigest()[:20]


def process_uses_profile(arguments, data_root):
    """Explicit profile arguments override the default; malformed commands never match."""
    try:
        values = [arg for arg in arguments if arg == "--data-dir" or arg.startswith("--data-dir=")]
        if len(values) > 1:
            return False
        if values:
            marker = values[0]
            value = (
                marker.split("=", 1)[1] if "=" in marker else arguments[arguments.index(marker) + 1]
            )
            path = Path(value)
            return path.is_absolute() and path.resolve() == Path(data_root).resolve()
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        return (local / "iPhoneIPAManagerCommunity").resolve() == Path(data_root).resolve()
    except IndexError, TypeError, ValueError, OSError:
        return False
