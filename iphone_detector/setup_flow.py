"""Shared setup steps and read-only saved-state checks."""

from pathlib import Path

STEPS = (
    (
        "Choose your IPA folder",
        "Save a permanent folder in Accounts & apps and copy completed IPA files into it.",
        "files",
        "accounts",
    ),
    (
        "Pair your iPhone",
        "Connect by USB, unlock, approve Trust This Computer and select the phone. Start with USB before configuring Wi-Fi.",
        "device",
        "pair",
    ),
    (
        "Add an Apple account",
        "Sign in, complete Apple's verification and assign one IPA to the account. Repeat for additional accounts.",
        "accounts",
        "login",
    ),
    (
        "Check your setup",
        "Review the read-only checklist. Saved pairing does not by itself prove a live connection. Then review and run your first installation.",
        "backend",
        "check",
    ),
    (
        "Optional remote and Discord control",
        "Configure a private bot, and establish the remote tunnel on home Wi-Fi before leaving. These features are optional for USB use.",
        "remote",
        "remote",
    ),
    (
        "Choose automatic renewal",
        "Enable the six-day schedule if wanted. The PC must be on and signed in; overdue runs wait for a usable phone connection.",
        "renewal",
        "renewal",
    ),
)


def inspect():
    from iphone_detector.config import load_config
    from iphone_detector.readiness import inspect as readiness

    value = readiness()
    config = load_config() or {}
    folder = config.get("automation", {}).get("ipa_directory")
    ready = isinstance(folder, str) and Path(folder).is_dir()
    for row in value["checks"]:
        if row["code"] == "files":
            row.update(
                status="ok" if ready else "error",
                detail="A saved IPA folder is available. Copy completed IPAs there before assigning them."
                if ready
                else "Choose an existing IPA folder first.",
                action="Use Choose IPA folder in Guided setup." if not ready else "",
            )
    return value
