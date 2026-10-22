"""Packaged-runtime checks without credentials, device access or installations."""

import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import uuid
import zipfile


def run(report_path):
    from iphone_detector.paths import FROZEN, ASSET_ROOT
    from iphone_detector.storage import write_json

    checks = []

    def check(name, action):
        try:
            action()
            checks.append({"name": name, "ok": True})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "error_type": type(exc).__name__})

    for module in (
        "discord",
        "psutil",
        "win32crypt",
        "wmi",
        "pymobiledevice3.remote.tunnel_service",
        "pymobiledevice3.services.afc",
    ):
        check("import:" + module, lambda name=module: importlib.import_module(name))

    def no_elevation():
        from iphone_detector.wifi import elevate_arguments

        try:
            elevate_arguments([])
        except RuntimeError:
            return
        raise RuntimeError("Elevation entry point was not disabled")

    check("administrator prompts disabled", no_elevation)

    def dynamic_accounts():
        from iphone_detector.settings import get_workflow
        from iphone_detector.auto_refresh import verified_success

        config = {
            "automation": {
                "accounts": [
                    {"email": "account%d@example.com" % index, "ipa": "App%d" % index}
                    for index in range(4)
                ]
            }
        }
        if len(get_workflow(config)) != 4 or not verified_success(
            {"ok": True, "completed_accounts": 4}, expected=4
        ):
            raise RuntimeError("Expanded account workflow was not verified")
        if verified_success({"ok": True, "completed_accounts": 3}, expected=4):
            raise RuntimeError("Partial renewal was accepted")

    check("expanded account completion checks", dynamic_accounts)
    with tempfile.TemporaryDirectory(prefix="ipa-manager-self-test-") as directory:
        folder = Path(directory)

        def storage():
            from concurrent.futures import ThreadPoolExecutor

            path = folder / "state.json"
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: write_json(path, {"index": i}), range(12)))
            if json.loads(path.read_text())["index"] not in range(12) or list(folder.glob("*.tmp")):
                raise RuntimeError("Incomplete state write")

        check("concurrent atomic storage", storage)

        def outbox():
            from iphone_detector.discord_outbox import enqueue, pending

            root = folder / "outbox-check"
            root.mkdir()
            audience = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            write_json(root / "discord.json", audience)
            if (
                not enqueue(root, "self-test", "Local test only")
                or len(pending(root, audience)) != 1
            ):
                raise RuntimeError("Operational report was not persisted")
            if pending(root, dict(audience, channel_id=4)):
                raise RuntimeError("Report audience isolation failed")

        check("durable private operational reports", outbox)

        def mutex():
            from iphone_detector.run_lock import RunLock

            first, second = RunLock(), RunLock()
            first.name = second.name = "Local\\IPAManagerSelfTest-" + uuid.uuid4().hex
            try:
                if not first.acquire() or second.acquire():
                    raise RuntimeError("Mutex exclusion failed")
                first.close()
                if not second.acquire():
                    raise RuntimeError("Mutex release failed")
            finally:
                first.close()
                second.close()

        check("installation mutex", mutex)

        def archive():
            from iphone_detector.ipa_inbox import validate_ipa

            path = folder / "sample.ipa"
            with zipfile.ZipFile(path, "w") as file:
                file.writestr("Payload/Test.app/Info.plist", b"plist")
            validate_ipa(path)
            with zipfile.ZipFile(path, "a") as file:
                file.writestr("../escape", b"bad")
            try:
                validate_ipa(path)
            except ValueError:
                return
            raise RuntimeError("Unsafe archive accepted")

        check("IPA archive validation", archive)
    binary = ASSET_ROOT / "tools/optimized-iloader/iloader-wifi.exe"

    def backend():
        with binary.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise ValueError("Invalid native backend")

    check("bundled native backend", backend)

    def account_auth_protocol():
        import subprocess

        process = subprocess.run(
            [str(binary), "--account-auth"],
            input=json.dumps(
                {
                    "email": "invalid",
                    "password": "self-test-placeholder",
                    "server": "https://example.com",
                }
            )
            + "\n",
            text=True,
            capture_output=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if process.returncode != 1 or "self-test-placeholder" in process.stdout + process.stderr:
            raise RuntimeError("Authentication input was not rejected safely")
        if json.loads(process.stdout.strip()).get("stage") != "failed":
            raise RuntimeError("Native authentication protocol unavailable")

    check("native account authentication protocol", account_auth_protocol)
    report = {
        "schema_version": 1,
        "ok": all(item["ok"] for item in checks),
        "frozen": FROZEN,
        "python": sys.version.split()[0],
        "checks": checks,
        "exe_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        if FROZEN
        else None,
    }
    write_json(report_path, report)
    return 0 if report["ok"] else 1
