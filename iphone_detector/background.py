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


def resolve_server(config):
    """Share server preference validation between installation and account sign-in."""
    server = config.get("automation", {}).get("anisette_server")
    if server is None:
        path = Path(os.environ.get("APPDATA", "")) / "me.nabdev.iloader/preferences.json"
        try:
            saved = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as exc:
            raise ValueError(
                "iloader preferences could not be read. Repair them locally or set an HTTPS anisette server in IPA Manager settings; the original file was preserved."
            ) from exc
        if not isinstance(saved, dict):
            raise ValueError(
                "iloader preferences must be a JSON object. Repair them locally or set an HTTPS anisette server in IPA Manager settings; the original file was preserved."
            )
        server = saved.get("newAnisetteServer", "new.ani.sidestore.io")
    from iphone_detector.input_integrity import https_server

    return https_server(server)


def make_job(config, transport, check_only=False, accounts=None, *, verify_content=True):
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
        from iphone_detector.input_integrity import identity

        entries.append(
            {
                "email": workflow[index]["email"],
                "ipa": str(ipa),
                "account": index + 1,
                "file_identity": identity(ipa, digest=verify_content),
            }
        )
    server = resolve_server(config)
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
    from iphone_detector.operations import selection_matches

    if not selection_matches(selected, current):
        raise ValueError(
            "The saved account or IPA changed after this run was requested. Use /accounts, then start a new run."
        )


def run_background(
    job, stop, transport="USB", check_only=False, request_override=None, accounts=None
):
    from iphone_detector.input_integrity import snapshot_inputs

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
    with (
        snapshot_inputs(job, request) as prepared,
        (job / "backend.log").open("w", encoding="utf-8") as output,
    ):
        from iphone_detector.storage import write_json

        native_path = job / "native-input.json"
        write_json(native_path, prepared)
        write_json(job / "native-dispatch.json", {"started_at": time.time()})
        process = subprocess.Popen(
            [str(executable), "--background-job", str(native_path)],
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
                    with events.open(encoding="utf-8", errors="replace") as file:
                        file.seek(offset)
                        while True:
                            position = file.tell()
                            line = file.readline()
                            if not line or not line.endswith("\n"):
                                file.seek(position)
                                break
                            if "\ufffd" in line:
                                LOG.warning("Ignored an undecodable backend progress event.")
                                continue
                            try:
                                data = json.loads(line)
                            except ValueError:
                                LOG.warning("Ignored a malformed backend progress event.")
                                continue
                            if not isinstance(data, dict) or not isinstance(data.get("stage"), str):
                                continue
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
            if not isinstance(result, dict):
                raise RuntimeError(
                    "Background iloader returned an invalid completion receipt. Inspect the phone before retrying."
                )
            expected = (
                0 if check_only or request.get("prepare_only") else len(request.get("accounts", []))
            )
            if (
                process.returncode
                or result.get("ok") is not True
                or type(result.get("completed_accounts")) is not int
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
