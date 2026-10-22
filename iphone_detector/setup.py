"""Read-only setup validation, suitable for the GUI and automated checks."""

from pathlib import Path

from iphone_detector.ipa_inbox import validate_ipa
from iphone_detector.paths import ASSET_ROOT
from iphone_detector.settings import get_workflow


def preflight(config):
    accounts = get_workflow(config)
    if not accounts:
        raise ValueError("Add an Apple account and choose its IPA first.")
    if not config.get("device", {}).get("serial"):
        raise ValueError("Connect your iPhone by USB, unlock it, and select it in the app.")
    folder = Path(config.get("automation", {}).get("ipa_directory", ""))
    if not folder.is_dir():
        raise ValueError("Choose an existing IPA folder and save settings.")
    for account in accounts:
        validate_ipa(folder / (account["ipa"] + ".ipa"))
    binary = ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe"
    if not binary.is_file():
        raise ValueError("The native backend is missing. Build it or use the packaged release.")
    return {"ok": True, "checked_accounts": len(accounts), "device_accessed": False}
