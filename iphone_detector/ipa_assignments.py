"""Persistent IPA changes stay pending until a verified full installation."""

from pathlib import Path


def mark_pending(config, index, stem):
    config.setdefault("automation", {}).setdefault("pending_ipa_installs", {})[str(index + 1)] = (
        stem
    )


def needs_install(config, number, stem):
    return config.get("automation", {}).get("pending_ipa_installs", {}).get(str(number)) == stem


def acknowledge_installs(job, result):
    """Use actual backend receipts; a profile-only renewal must never clear a new IPA."""
    import json
    from iphone_detector.config import load_config, save_config
    from iphone_detector.settings import get_workflow

    if not result.get("ok") or result.get("prepared_only"):
        return
    request_path = Path(job) / "backend-job.json"
    if not request_path.exists():
        return
    request = json.loads(request_path.read_text(encoding="utf-8"))
    entries = request.get("accounts", [])
    if not entries or result.get("completed_accounts") != len(entries):
        return
    installed = (
        result.get("installed_accounts", [])
        if request.get("profile_refresh")
        else [entry.get("account", index + 1) for index, entry in enumerate(entries)]
    )
    selection_path = Path(job) / "selection.json"
    selection = (
        json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else {}
    )
    selected_entries = selection.get("accounts", []) if isinstance(selection, dict) else []
    config = load_config() or {}
    workflow = get_workflow(config)
    pending = config.get("automation", {}).get("pending_ipa_installs", {})
    changed = False
    for index, entry in enumerate(entries):
        number = entry.get("account", index + 1)
        stem = Path(entry["ipa"]).stem
        identity = entry.get("file_identity")
        if identity is None:
            identity = next(
                (
                    chosen.get("file_identity")
                    for chosen in selected_entries
                    if chosen.get("account") == number and chosen.get("ipa") == entry["ipa"]
                ),
                None,
            )
        if identity is not None:
            try:
                stat = Path(entry["ipa"]).stat()
            except OSError:
                continue
            if identity != {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}:
                continue  # The source was replaced during this run. Keep it pending.
        if (
            type(number) is int
            and 1 <= number <= len(workflow)
            and number in installed
            and workflow[number - 1]["ipa"] == stem
            and pending.get(str(number)) == stem
        ):
            del pending[str(number)]
            changed = True
    if changed:
        save_config(config)
