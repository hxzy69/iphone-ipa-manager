"""Verify packaged queue and scheduler failure paths using disposable fake inputs.

No phone, Apple credentials, Discord connection or existing settings are used.
Every scenario fails before device work and preserves its input for inspection.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import psutil


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def run(executable, root, *arguments):
    process = subprocess.Popen(
        [str(executable), "--data-dir", str(root), *arguments],
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=dict(os.environ, IPA_MANAGER_DATA_DIR=str(root), PYINSTALLER_RESET_ENVIRONMENT="1"),
    )
    try:
        return process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.wait(timeout=10)
        _, alive = psutil.wait_procs(children, timeout=10)
        if alive:
            raise RuntimeError("Timed-out test children could not be stopped.") from None
        raise


def scenario(root):
    account = {
        "account": 1,
        "email": "fixture@example.com",
        "ipa": str(root / "missing.ipa"),
        "file_identity": {"size": 10, "mtime_ns": 1, "sha256": "0" * 64},
    }
    selection = {"serial": "fixture-not-a-real-phone", "accounts": [account]}
    write(
        root / "config.json",
        {
            "device": {"serial": selection["serial"]},
            "automation": {
                "ipa_directory": str(root),
                "accounts": [{"email": account["email"], "ipa": "missing"}],
            },
        },
    )
    write(root / "discord.json", {"user_id": 1, "guild_id": 2, "channel_id": 3})
    entry = {
        "id": "fixture",
        "mode": "install",
        "state": "queued",
        "created_at": time.time(),
        "selection": selection,
    }
    path = root / "queue/fixture.json"
    write(path, entry)
    return path, entry


def assert_no_device_work(root):
    for name in ("backend-events.jsonl", "native-input.json", "remote-request.json"):
        assert not list(root.rglob(name)), "Unexpected device submission: " + name


def smoke(executable):
    executable = Path(executable).resolve()
    checks = []
    with tempfile.TemporaryDirectory(prefix="ipa-recovery-acceptance-") as directory:
        base = Path(directory)
        root = base / "missing-file"
        path, _ = scenario(root)
        assert run(executable, root, "--queue-runner") == 0
        failed = json.loads(path.read_text())
        assert failed["state"] == "failed" and failed["failure_code"] == "selection-changed"
        reports = list((root / "discord-outbox").glob("*.json"))
        assert len(reports) == 1
        assert "phone, account or IPA changed" in json.loads(reports[0].read_text())["message"]
        assert run(executable, root, "--queue-runner") == 0
        assert len(list((root / "discord-outbox").glob("*.json"))) == 1
        assert_no_device_work(root)
        checks.append("missing-ipa-pauses-once-with-durable-report-no-replay")

        root = base / "wrong-receipt"
        path, entry = scenario(root)
        write(path, dict(entry, state="running"))
        job = root / "automation-runs/queue-fixture"
        write(job / "selection.json", dict(entry["selection"], serial="different-fixture"))
        write(job / "result.json", {"ok": True, "completed_accounts": 1})
        assert run(executable, root, "--queue-runner") == 0
        value = json.loads(path.read_text())
        assert value["state"] == "failed" and value["failure_code"] == "completion-unverified"
        assert_no_device_work(root)
        checks.append("different-phone-receipt-never-counted-completed")

        root = base / "untyped-account-receipt"
        path, entry = scenario(root)
        write(path, dict(entry, state="running"))
        job = root / "automation-runs/queue-fixture"
        altered = json.loads(json.dumps(entry["selection"]))
        altered["accounts"][0]["account"] = True
        write(job / "selection.json", altered)
        write(job / "result.json", {"ok": True, "completed_accounts": 1})
        assert run(executable, root, "--queue-runner") == 0
        value = json.loads(path.read_text())
        assert value["state"] == "failed" and value["failure_code"] == "completion-unverified"
        assert_no_device_work(root)
        checks.append("boolean-account-receipt-pauses-without-replay")

        root = base / "corrupt-queue"
        path, _ = scenario(root)
        path.write_text("{broken", encoding="utf-8")
        assert run(executable, root, "--queue-runner") != 0
        assert path.read_text() == "{broken"
        assert_no_device_work(root)
        checks.append("corrupt-queue-preserved-without-dispatch")

        root = base / "corrupt-renewal"
        root.mkdir()
        (root / "auto-refresh.json").write_text("{broken", encoding="utf-8")
        assert run(executable, root, "--auto-refresh") == 0
        value = json.loads((root / "auto-refresh-check.json").read_text())
        assert value["status"] == "state-needs-repair"
        assert (root / "auto-refresh.json").read_text() == "{broken"
        assert_no_device_work(root)
        checks.append("corrupt-renewal-pauses-with-repair-status")

        root = base / "invalid-preferences"
        root.mkdir()
        original = '{"automation":{"install_timeout":"invalid"}}'
        (root / "config.json").write_text(original, encoding="utf-8")
        job = root / "automation-runs/fixture"
        assert run(executable, root, "--worker", "discord-run", "--job-dir", str(job)) == 1
        assert json.loads((job / "result.json").read_text())["error_code"] == "CONFIG_UNREADABLE"
        assert (root / "config.json").read_text() == original
        assert_no_device_work(root)
        checks.append("invalid-preference-types-preserved-before-worker-launch")

        root = base / "invalid-remote-settings"
        scenario(root)
        (root / "remote.json").write_text("{broken", encoding="utf-8")
        job = root / "automation-runs/fixture"
        assert run(executable, root, "--worker", "discord-run", "--job-dir", str(job)) == 1
        assert (
            json.loads((job / "result.json").read_text())["error_code"]
            == "REMOTE_SETTINGS_UNREADABLE"
        )
        assert (root / "remote.json").read_text() == "{broken"
        assert_no_device_work(root)
        checks.append("invalid-remote-settings-have-a-final-failure-receipt")
    return {
        "ok": True,
        "exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "checks": checks,
        "phone_writes": False,
        "private_profile_used": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = smoke(args.executable)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
