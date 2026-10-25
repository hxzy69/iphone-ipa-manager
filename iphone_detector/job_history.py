"""Read a bounded, credential-free summary of recent runs."""

import json


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except OSError, ValueError:
        return {}


def describe(job):
    """Use the same conservative outcome in the GUI and Discord history."""
    result = read(job / "result.json")
    request = read(job / "backend-job.json") or read(job / "selection.json")
    raw = request.get("accounts")
    valid = (
        isinstance(raw, list)
        and 1 <= len(raw) <= 25
        and all(
            isinstance(a, dict) and type(a.get("account")) is int and 1 <= a["account"] <= 25
            for a in raw
        )
    )
    accounts = [a["account"] for a in raw] if valid else []
    valid = valid and len(set(accounts)) == len(accounts)
    verified = (
        valid
        and result.get("ok") is True
        and type(result.get("completed_accounts")) is int
        and result["completed_accounts"] == len(accounts)
        and not result.get("prepared_only")
    )
    status = (
        "Completed"
        if verified
        else "Prepared only"
        if result.get("ok") is True and result.get("prepared_only")
        else "Cancelled"
        if result.get("cancelled") is True
        else "Needs attention"
        if result
        else "Unfinished or running"
    )
    route = result.get("transport") or read(job / "route.json").get("transport")
    return {
        "status": status,
        "accounts": accounts,
        "operation": "Refresh"
        if request.get("profile_refresh")
        or "refreshed_accounts" in result
        or (job / "refresh-request").exists()
        else "Install",
        "transport": {"usb": "USB", "local-wifi": "local Wi-Fi", "remote": "remote tunnel"}.get(
            route, ""
        )
        if isinstance(route, str)
        else "",
    }


def recent_jobs(root, limit=5):
    candidates = []
    for folder, prefixes in (
        (root / "automation-runs", ("discord-", "automatic-", "gui-", "queue-")),
        (root / "gui-jobs", None),
    ):
        if not folder.is_dir():
            continue
        for job in folder.iterdir():
            try:
                if (
                    job.is_symlink()
                    or not job.is_dir()
                    or (prefixes and not job.name.startswith(prefixes))
                ):
                    continue
                candidates.append((job.stat().st_mtime, job))
            except OSError:
                continue
    entries = []
    for updated, job in sorted(candidates, key=lambda entry: entry[0], reverse=True)[
        : max(1, min(10, limit))
    ]:
        entries.append(dict(describe(job), updated=updated))
    return entries
