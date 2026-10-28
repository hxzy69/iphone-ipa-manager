"""Bounded assignment history stored atomically with the account settings."""

from datetime import datetime, timezone
from pathlib import Path
import uuid

from iphone_detector.settings import get_workflow, ipa_stem

LIMIT = 100


def entries(config):
    raw = config.get("assignment_history", [])
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[-LIMIT:]:
        try:
            if not isinstance(item, dict):
                continue
            uuid.UUID(item["id"])
            datetime.fromisoformat(item["changed_at"])
            if not isinstance(item["email"], str) or len(item["email"]) > 254:
                continue
            for key in ("previous", "current"):
                value = item[key]
                if (
                    not isinstance(value, dict)
                    or not isinstance(value["directory"], str)
                    or not isinstance(value["ipa"], str)
                    or len(value["directory"]) > 32767
                ):
                    raise ValueError("Invalid assignment history")
                ipa_stem(value["ipa"])
                signature = value.get("signature")
                if signature is not None and (
                    not isinstance(signature, (list, tuple))
                    or len(signature) != 2
                    or any(type(n) is not int or n < 0 for n in signature)
                ):
                    raise ValueError("Invalid file signature")
            clean = {key: item[key] for key in ("id", "changed_at", "email")}
            for key in ("previous", "current"):
                clean[key] = {
                    name: item[key].get(name) for name in ("ipa", "directory", "signature")
                }
            result.append(clean)
        except KeyError, ValueError, TypeError, AttributeError:
            continue
    return result


def _assignment(config, account):
    directory = config.get("automation", {}).get(
        "ipa_directory", str(Path.home() / "Downloads/ipas")
    )
    signature = None
    try:
        path = Path(directory) / (account["ipa"] + ".ipa")
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            signature = [stat.st_size, stat.st_mtime_ns]
    except OSError:
        pass
    return {"ipa": account["ipa"], "directory": directory, "signature": signature}


def record(previous, current):
    """Called only by config.save_config; changes and history share one atomic write."""
    history = entries(previous)
    try:
        old = {item["email"]: item for item in get_workflow(previous)}
    except ValueError:
        old = {}
    for account in get_workflow(current):
        before = old.get(account["email"])
        if before is None:
            continue
        old_folder = previous.get("automation", {}).get("ipa_directory")
        new_folder = current.get("automation", {}).get("ipa_directory")
        if before["ipa"] == account["ipa"] and old_folder == new_folder:
            continue
        history.append(
            {
                "id": str(uuid.uuid4()),
                "changed_at": datetime.now(timezone.utc).isoformat(),
                "email": account["email"],
                "previous": _assignment(previous, before),
                "current": _assignment(current, account),
            }
        )
    if history or "assignment_history" in current:
        current["assignment_history"] = history[-LIMIT:]


def undo(identifier):
    """Restore a still-available IPA through the normal locked assignment path."""
    from iphone_detector.config import load_config
    from iphone_detector.discord_inbox import assign_managed

    config = load_config() or {}
    item = next((row for row in entries(config) if row["id"] == identifier), None)
    if item is None:
        raise ValueError("This history entry is no longer available. Refresh history.")
    accounts = get_workflow(config)
    index = next(
        (i for i, account in enumerate(accounts) if account["email"] == item["email"]), None
    )
    folder = Path(config.get("automation", {}).get("ipa_directory", ""))
    if (
        index is None
        or accounts[index]["ipa"] != item["current"]["ipa"]
        or folder != Path(item["current"]["directory"])
    ):
        raise ValueError("The account assignment changed since this entry. Refresh history.")
    source = folder / (item["previous"]["ipa"] + ".ipa")
    if not source.is_file() or source.is_symlink():
        raise ValueError(
            "The previous IPA is not in the current IPA folder. Restore the file first."
        )
    stat = source.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    previous_signature = item["previous"].get("signature")
    if previous_signature is not None and tuple(previous_signature) != signature:
        raise ValueError(
            "The previous IPA file has changed. Select it explicitly in the library instead."
        )
    assign_managed(
        source,
        signature,
        index,
        expected_email=item["email"],
        expected_ipa=item["current"]["ipa"],
        expected_directory=str(folder),
    )
    return index + 1
