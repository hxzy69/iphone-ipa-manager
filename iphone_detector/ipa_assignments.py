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
    from iphone_detector.config import load_config, save_config
    from iphone_detector.settings import get_workflow
    from iphone_detector.job_history import read
    from iphone_detector.wifi import canonical_serial

    if not isinstance(result, dict) or result.get("prepared_only"):
        return
    request = read(Path(job) / "backend-job.json")
    entries = request.get("accounts", [])
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        return
    if (
        result.get("ok") is not True
        or type(result.get("completed_accounts")) is not int
        or result["completed_accounts"] != len(entries)
    ):
        from iphone_detector.operations import matching

        installed = [
            entry["account"]
            for entry in entries
            if matching(read(Path(job) / f"completed-{entry.get('account')}.json"), request, entry)
            and read(Path(job) / f"completed-{entry.get('account')}.json").get("operation")
            == "install"
        ]
        if not installed:
            return
    else:
        installed = (
            result.get("installed_accounts", [])
            if request.get("profile_refresh")
            else [entry.get("account", index + 1) for index, entry in enumerate(entries)]
        )
    if not isinstance(installed, list) or any(type(number) is not int for number in installed):
        return
    selection = read(Path(job) / "selection.json")
    selected_entries = selection.get("accounts", [])
    if not isinstance(selected_entries, list):
        selected_entries = []
    config = load_config() or {}
    serial = config.get("device", {}).get("serial")
    if (
        not isinstance(serial, str)
        or not isinstance(request.get("serial"), str)
        or canonical_serial(serial) != canonical_serial(request["serial"])
        or not serial
    ):
        return
    workflow = get_workflow(config)
    pending = config.get("automation", {}).get("pending_ipa_installs", {})
    changed = False
    for index, entry in enumerate(entries):
        number = entry.get("account", index + 1)
        if type(number) is not int or not 1 <= number <= len(workflow) or number not in installed:
            continue
        email = entry.get("email")
        if (
            not isinstance(email, str)
            or workflow[number - 1]["email"].casefold() != email.casefold()
        ):
            continue
        if not isinstance(entry.get("ipa"), str):
            continue
        stem = Path(entry["ipa"]).stem
        folder = Path(
            config.get("automation", {}).get("ipa_directory", Path.home() / "Downloads/ipas")
        )
        if (
            Path(entry["ipa"]).resolve()
            != (folder / (workflow[number - 1]["ipa"] + ".ipa")).resolve()
        ):
            continue
        identity = entry.get("file_identity")
        if identity is None:
            identity = next(
                (
                    chosen.get("file_identity")
                    for chosen in selected_entries
                    if isinstance(chosen, dict)
                    and chosen.get("account") == number
                    and chosen.get("ipa") == entry["ipa"]
                    and chosen.get("email") == email
                ),
                None,
            )
        if isinstance(identity, dict) and all(
            key in identity for key in ("size", "mtime_ns", "sha256")
        ):
            from iphone_detector.input_integrity import identity as content_identity

            try:
                actual = content_identity(Path(entry["ipa"]), digest="sha256" in identity)
            except OSError, ValueError:
                continue
            if identity != actual:
                continue  # The source was replaced during this run. Keep it pending.
        else:
            continue  # Legacy or damaged snapshots cannot acknowledge a new assignment.
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
