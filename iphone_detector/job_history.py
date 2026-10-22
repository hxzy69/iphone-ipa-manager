"""Read a bounded, credential-free summary of recent runs."""

import json


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except OSError, ValueError:
        return {}


def recent_jobs(root, limit=5):
    candidates = []
    for folder, prefixes in (
        (root / "automation-runs", ("discord-", "automatic-", "gui-")),
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
        result = read(job / "result.json")
        request = read(job / "backend-job.json")
        raw_accounts = request.get("accounts", [])
        if not isinstance(raw_accounts, list):
            raw_accounts = []
        accounts = [
            entry.get("account", index + 1)
            for index, entry in enumerate(raw_accounts)
            if isinstance(entry, dict)
        ]
        accounts = [number for number in accounts if type(number) is int and 1 <= number <= 25]
        complete = result.get("completed_accounts")
        expected = len(accounts) if accounts else 3
        verified = result.get("ok") is True and type(complete) is int and complete == expected
        status = (
            "Completed"
            if verified
            else "Cancelled"
            if result.get("cancelled")
            else "Needs attention"
            if result
            else "Unfinished or running"
        )
        route = result.get("transport") or read(job / "route.json").get("transport")
        entries.append(
            {
                "updated": updated,
                "status": status,
                "accounts": accounts,
                "operation": "Refresh"
                if request.get("profile_refresh")
                or "refreshed_accounts" in result
                or (job / "refresh-request").exists()
                else "Install",
                "transport": {"local-wifi": "local Wi-Fi", "remote": "remote tunnel"}.get(
                    route, ""
                ),
            }
        )
    return entries
