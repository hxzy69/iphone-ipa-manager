"""Resolve immutable application assets and private, per-user state."""

import os
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
