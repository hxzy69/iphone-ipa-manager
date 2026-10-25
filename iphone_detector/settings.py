"""Editable workflow settings, independent of GUI and vision dependencies."""

import re
from pathlib import Path

DEFAULT_ACCOUNTS = []
MAX_ACCOUNTS = 25


def ipa_stem(value):
    value = str(value).strip()
    if value.lower().endswith(".ipa"):
        value = value[:-4]
    if (
        not value
        or value in (".", "..")
        or value.endswith((".", " "))
        or len(value) > 251
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
    ):
        raise ValueError(
            "Enter an IPA filename, without a folder path or Windows-reserved characters."
        )
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value):
        raise ValueError("That filename is reserved by Windows.")
    return value


def get_workflow(config=None):
    if config is None:
        from iphone_detector.config import load_config

        config = load_config() or {}
    if not isinstance(config, dict) or not isinstance(config.get("automation", {}), dict):
        raise ValueError("Settings and automation settings must be JSON objects.")
    overrides = config.get("automation", {}).get("accounts", [])
    if not isinstance(overrides, list) or len(overrides) > MAX_ACCOUNTS:
        raise ValueError("Account settings must contain at most 25 accounts.")
    workflow = []
    seen = set()
    for index, entry in enumerate(overrides):
        if not isinstance(entry, dict):
            raise ValueError("Invalid settings for account %d." % (index + 1))
        if not isinstance(entry.get("email"), str) or not isinstance(entry.get("ipa"), str):
            raise ValueError("Account email and IPA filename must be text.")
        email = str(entry.get("email", "")).strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("Invalid email for account %d." % (index + 1))
        if email in seen:
            raise ValueError("Each account must have a different email address.")
        seen.add(email)
        ipa = ipa_stem(entry.get("ipa", ""))
        words = re.findall(r"[A-Za-z]{3,}", ipa)
        identifier = words[0].lower() if words else re.sub("[^a-z0-9]", "", ipa.lower())
        if not identifier:
            raise ValueError("The IPA filename needs a readable app name.")
        workflow.append(
            {
                "name": "account %d" % (index + 1),
                "email": email,
                "ipa": ipa,
                "identifier": identifier,
            }
        )
    return workflow


def update_settings(config, folder, filenames, timeout_minutes=15, close_after=False):
    import copy

    result = copy.deepcopy(config)
    if len(filenames) != len(get_workflow(config)):
        raise ValueError("Provide one IPA filename for every configured account.")
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise ValueError("Choose an existing folder containing your IPA files.")
    minutes = int(timeout_minutes)
    if not 1 <= minutes <= 120:
        raise ValueError("Installation timeout must be between 1 and 120 minutes.")
    automation = result.setdefault("automation", {})
    existing = get_workflow(config)
    automation["accounts"] = [
        {"email": account["email"], "ipa": ipa_stem(name)}
        for account, name in zip(existing, filenames)
    ]
    from iphone_detector.ipa_assignments import mark_pending

    for index, (old, new) in enumerate(zip(existing, automation["accounts"])):
        if (
            old["ipa"] != new["ipa"]
            or Path(automation.get("ipa_directory", folder)).resolve() != folder.resolve()
        ):
            mark_pending(result, index, new["ipa"])
    automation["ipa_directory"] = str(folder.resolve())
    automation["install_timeout"] = minutes * 60
    automation["close_gui_after_success"] = bool(close_after)
    automation["background_mode"] = True
    return result
