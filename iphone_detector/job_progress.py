"""Credential-free stages and timing from bounded worker receipts."""

import json
import time
import re
from iphone_detector.job_history import read
from iphone_detector.storage import finite_number

STAGES = (
    "Preparing",
    "Signing in",
    "Signing",
    "Transferring",
    "Installing",
    "Verifying",
    "Completed",
)
LABELS = {
    "connection_verified": "Preparing",
    "login": "Signing in",
    "login_retry": "Signing in",
    "prepare_profiles": "Signing",
    "sign": "Signing",
    "prepared_account": "Signing",
    "upload": "Transferring",
    "install": "Installing",
    "verify": "Verifying",
    "refresh": "Installing",
    "completed_account": "Completed",
}


def elapsed(job, now=None):
    worker = read(job / "worker.json")
    start = worker.get("started_at")
    if not finite_number(start) or start <= 0:
        return ""
    end = worker.get("finished_at") if worker.get("state") == "finished" else now or time.time()
    if not finite_number(end):
        return ""
    seconds = max(0, int(end - start))
    return "%dm %02ds elapsed" % divmod(seconds, 60)


def failure_hint(result):
    if result.get("cancelled"):
        return "Cancelled. Check any completed accounts before starting another run."
    text = str(result.get("error", "")).casefold()
    if any(word in text for word in ("password", "sign-in", "login", "authentication")):
        return "Apple sign-in needs attention. Check the account and verification requirements, then retry."
    if any(word in text for word in ("ipa", "archive", "metadata")):
        return (
            "Check the assigned IPA in the library; finish its transfer or replace a damaged file."
        )
    if any(word in text for word in ("connect", "tunnel", "offline", "device")):
        return (
            "Check the unlocked iPhone and its connection. Inspect the run result before retrying."
        )
    return "Inspect the activity log and verified account outcomes before retrying; an uncertain installation is not automatically repeated."


def with_elapsed(job, text):
    # Controller.state may already contain the previous poll's timing suffix.
    text = re.sub(r"(?: \d+m \d{2}s elapsed)+$", "", text)
    timing = elapsed(job)
    return text + (" " + timing if timing else "")


def snapshot(job):
    rows = {}
    try:
        with (job / "backend-events.jsonl").open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 262144))
            lines = stream.read(262144).decode("utf-8", errors="replace").splitlines()
        for line in lines:
            try:
                event = json.loads(line)
                number = event.get("account")
                if type(number) is int and 1 <= number <= 25 and event.get("stage") in LABELS:
                    rows[number] = LABELS[event["stage"]]
            except ValueError, AttributeError, TypeError:
                continue
    except OSError:
        pass
    transfer = read(job / "remote-transfer.json")
    number = transfer.get("account")
    if (
        type(number) is int
        and 1 <= number <= 25
        and isinstance(transfer.get("stage"), str)
        and transfer.get("stage") in LABELS
        and rows.get(number) != "Completed"
    ):
        rows[number] = LABELS[transfer["stage"]]
    result = read(job / "result.json")
    worker = read(job / "worker.json")
    for number in worker.get("accounts", []) if isinstance(worker.get("accounts"), list) else []:
        if type(number) is int and 1 <= number <= 25:
            rows.setdefault(number, "Preparing")
    verified = (
        result.get("ok") is True
        and type(result.get("completed_accounts")) is int
        and result["completed_accounts"] == len(rows)
        and rows
        and not result.get("prepared_only")
    )
    prepared = result.get("ok") is True and result.get("prepared_only") is True
    if verified:
        rows = {number: "Completed" for number in rows}
    state = (
        "Completed"
        if verified
        else "Prepared only"
        if prepared
        else "Cancelled"
        if result.get("cancelled")
        else "Needs attention"
        if result
        else "Working"
    )
    return {
        "state": state,
        "elapsed": elapsed(job),
        "accounts": [{"account": n, "stage": stage} for n, stage in sorted(rows.items())],
        "hint": "Preparation completed; no app installation was verified."
        if prepared
        else failure_hint(result)
        if result and not verified
        else "Progress follows backend receipts; refreshes may skip transfer and installation stages.",
    }
