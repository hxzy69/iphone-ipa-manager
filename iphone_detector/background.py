"""Run iloader's native backend without starting its UI or generating input."""

import json
import logging
import os
from pathlib import Path
import subprocess
import time
import re

from iphone_detector.config import load_config
from iphone_detector.paths import ASSET_ROOT, DATA_ROOT
from iphone_detector.settings import get_workflow
from iphone_detector.wifi import cache_executable

LOG = logging.getLogger("iphone-detector.background")


def error_summary(message):
    lines = [line.strip(" \t│├└╰─●┬┤┼") for line in str(message).splitlines()]
    lines = [line for line in lines if line and not re.search(r"\.rs:\d+$", line)]
    if not lines:
        return "Background iloader failed. See backend.log."
    return (lines[0] if len(lines) == 1 else lines[0].rstrip(": ") + ": " + lines[-1])[:900]


class BackgroundStop:
    def __init__(self, event):
        self.cancelled = event

    def check(self):
        if self.cancelled.is_set():
            from iphone_detector.cancellation import Cancelled

            raise Cancelled("Background installation stopped")

    def sleep(self, seconds):
        self.cancelled.wait(seconds)
        self.check()


def selected_accounts(workflow, accounts=None):
    """Map account indices (0-based, unordered, unvalidated) to an ordered unique subset."""
    if not workflow:
        raise ValueError("Add an Apple account and assign an IPA before starting a run.")
    if accounts is None:
        return list(range(len(workflow)))
    chosen = []
    for account in accounts:
        if type(account) is not int or not 0 <= account < len(workflow):
            raise ValueError("Choose one of the configured account numbers.")
        if account not in chosen:
            chosen.append(account)
    if not chosen:
        raise ValueError("Choose at least one account.")
    return sorted(chosen)


def make_job(config, transport, check_only=False, accounts=None):
    serial = config.get("device", {}).get("serial")
    if not serial:
        raise ValueError("Pair your iPhone by USB first.")
    automation = config.get("automation", {})
    folder = Path(automation.get("ipa_directory", Path.home() / "Downloads/ipas")).resolve()
    workflow = get_workflow(config)
    entries = []
    for index in selected_accounts(workflow, accounts):
        ipa = folder / (workflow[index]["ipa"] + ".ipa")
        if not ipa.is_file():
            raise ValueError("IPA file not found: " + str(ipa))
        # The backend keys its per-account artifacts and events by this stable number.
        stat = ipa.stat()
        entries.append(
            {
                "email": workflow[index]["email"],
                "ipa": str(ipa),
                "account": index + 1,
                "file_identity": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
            }
        )
    settings = Path(os.environ.get("APPDATA", "")) / "me.nabdev.iloader/preferences.json"
    saved = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    server = saved.get("newAnisetteServer", "new.ani.sidestore.io")
    if not server.startswith(("https://", "http://")):
        server = "https://" + server
    return {
        "serial": serial,
        "transport": transport,
        "accounts": entries,
        "parent_pid": os.getpid(),
        "anisette_server": server,
        "fast": bool(automation.get("wifi_archive_transfer", True)),
        "timeout": max(30, int(automation.get("install_timeout", 900))),
        "check_only": check_only,
    }


def validate_selection(job, config, accounts=None):
    """A Discord request must not install a different assignment after worker startup."""
    path = job / "selection.json"
    if not path.exists():
        return
    selected = json.loads(path.read_text(encoding="utf-8"))
    current = make_job(config, "Network", accounts=accounts)
    if not isinstance(selected, dict) or any(
        selected.get(key) != current.get(key) for key in ("serial", "accounts")
    ):
        raise ValueError(
            "The saved account or IPA changed after this run was requested. Use /accounts, then start a new run."
        )


def run_background(
    job, stop, transport="USB", check_only=False, request_override=None, accounts=None
):
    config = load_config() or {}
    request = (
        request_override
        if request_override is not None
        else make_job(config, transport, check_only, accounts)
    )
    binary = ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe"
    if not binary.is_file():
        raise RuntimeError(
            "The bundled background iloader is missing. Use the current standalone build."
        )
    executable = cache_executable(binary, DATA_ROOT / "runtime/iloader-wifi")
    request_path = job / "backend-job.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    LOG.info(
        "Starting native background iloader (%s). No window, OCR, clipboard or input automation.",
        transport,
    )
    with (job / "backend.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(executable), "--background-job", str(request_path)],
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        offset = 0
        text_offset = 0
        stop_started = None
        try:
            while process.poll() is None:
                if stop.cancelled.is_set() and stop_started is None:
                    (job / "backend-stop").touch()
                    stop_started = time.monotonic()
                if stop_started and time.monotonic() - stop_started > 30:
                    process.terminate()
                events = job / "backend-events.jsonl"
                if events.exists():
                    with events.open(encoding="utf-8") as file:
                        file.seek(offset)
                        while True:
                            position = file.tell()
                            line = file.readline()
                            if not line or not line.endswith("\n"):
                                file.seek(position)
                                break
                            data = json.loads(line)
                            LOG.info(
                                "[background] %s%s",
                                data["stage"],
                                " — account %s" % data["account"] if data.get("account") else "",
                            )
                        offset = file.tell()
                with (job / "backend.log").open(encoding="utf-8", errors="replace") as file:
                    file.seek(text_offset)
                    for _ in range(200):
                        position = file.tell()
                        line = file.readline()
                        if not line or not line.endswith("\n"):
                            file.seek(position)
                            break
                        LOG.info("[iloader] %s", line.rstrip())
                    text_offset = file.tell()
                time.sleep(0.2)
            stop.check()
            result_path = job / "backend-result.json"
            result = (
                json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            )
            expected = (
                0 if check_only or request.get("prepare_only") else len(request.get("accounts", []))
            )
            if (
                process.returncode
                or not result.get("ok")
                or result.get("completed_accounts") != expected
                or (request.get("prepare_only") and result.get("prepared_only") is not True)
            ):
                details = result.get(
                    "error", "Background iloader stopped unexpectedly. See backend.log."
                )
                LOG.error("Background backend details: %s", details)
                raise RuntimeError(error_summary(details))
            LOG.info(
                "Background %s verified complete.",
                "signing export"
                if request.get("prepare_only")
                else "connection check"
                if check_only
                else "%d-account installation" % expected,
            )
            return result
        finally:
            if process.poll() is None:
                (job / "backend-stop").touch()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
