"""Persistent, user-level RemotePairing transport and serialized install queue."""

import asyncio
from contextlib import AsyncExitStack
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import plistlib
import zipfile

from iphone_detector.paths import DATA_ROOT
from iphone_detector.wifi import canonical_serial

LOG = logging.getLogger("iphone-detector.remote")
SETTINGS = DATA_ROOT / "remote.json"
STATE = DATA_ROOT / "remote-state.json"
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def atomic(path, value):
    from iphone_detector.storage import write_json

    write_json(path, value)


def settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}


def enabled():
    return bool(settings().get("enabled"))


def state():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("state") in ("ready", "installing", "waiting", "connecting", "offline")
            and type(data.get("updated")) in (int, float)
            and math.isfinite(data["updated"])
            and -5 <= time.time() - data["updated"] < 45
        ):
            return data
    except PermissionError:
        return {"state": "connecting"}
    except OSError, ValueError, KeyError:
        pass
    return {"state": "offline"}


def describe():
    value = state()["state"]
    return {
        "ready": "Remote tunnel ready.",
        "installing": "Remote installation running.",
        "waiting": "Remote tunnel waiting for the iPhone. Join Wi-Fi with Tailscale connected.",
        "connecting": "Establishing the remote tunnel.",
    }.get(value, "Remote tunnel is offline.")


def ensure_started():
    if not enabled():
        return
    if state()["state"] != "offline":
        return
    from iphone_detector.discord_bot import command

    subprocess.Popen(
        command(["--remote-daemon"]),
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
    )


def submit(job, stop, accounts=None):
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config

    if job.resolve().parent != (DATA_ROOT / "automation-runs").resolve():
        raise ValueError("Remote jobs must be created in the application automation-runs folder.")
    if state()["state"] != "ready":
        raise RuntimeError(describe())
    request = make_job(load_config() or {}, "Remote", accounts=accounts)
    selected = [entry["account"] for entry in request.get("accounts", [])]
    (job / "remote-heartbeat").touch()
    atomic(job / "remote-request.json", {"created": time.time(), "accounts": selected})
    offline_since = None
    try:
        while not (job / "remote-result.json").exists():
            stop.check()
            (job / "remote-heartbeat").touch()
            if state()["state"] == "offline":
                offline_since = offline_since or time.monotonic()
                if time.monotonic() - offline_since > 15:
                    raise RuntimeError(
                        "Remote tunnel process stopped. Inspect the phone before retrying."
                    )
            else:
                offline_since = None
            stop.sleep(0.5)
    finally:
        if not (job / "remote-result.json").exists():
            (job / "stop").touch()
    result = json.loads((job / "remote-result.json").read_text(encoding="utf-8"))
    if not result.get("ok") or result.get("completed_accounts") != len(selected):
        raise RuntimeError(result.get("error", "Remote installation did not complete."))
    return result


def bundle_id(path):
    with zipfile.ZipFile(path) as archive:
        names = [
            n
            for n in archive.namelist()
            if n.startswith("Payload/") and n.count("/") == 2 and n.endswith(".app/Info.plist")
        ]
        if len(names) != 1:
            raise RuntimeError("Signed IPA must contain exactly one main app.")
        value = plistlib.loads(archive.read(names[0])).get("CFBundleIdentifier")
        if not isinstance(value, str) or not value:
            raise RuntimeError("Signed IPA has no bundle identifier.")
        return value


async def lookup(rsd):
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    async with InstallationProxyService(rsd) as installer:
        result = await installer.lookup(
            {"ApplicationType": "User", "ReturnAttributes": ["CFBundleIdentifier"]}
        )
        if not isinstance(result, dict):
            raise RuntimeError("Installation service returned an invalid response.")
        return result


def compression_level():
    try:
        from iphone_detector.connection_router import tailscale_candidate

        return 6 if tailscale_candidate(settings()) else 9
    except Exception:
        return 9


def upload_streams():
    """Use the measured two-stream path only for a direct public remote endpoint."""
    try:
        import ipaddress
        from iphone_detector.connection_router import tailscale_candidate

        endpoint = tailscale_candidate(settings())
        return 2 if endpoint and ipaddress.ip_address(endpoint).is_global else 1
    except Exception:
        return 1


def transfer_progress(path, timing, stage):
    try:
        atomic(
            path.parent / "remote-transfer.json",
            {
                "stage": stage,
                "account": timing.get("account"),
                "uploaded_bytes": timing["uploaded_bytes"],
                "archive_bytes": timing["archive_bytes"],
                "upload_started_at": timing.get("upload_started_at"),
                "updated_at": time.time(),
            },
        )
    except OSError:
        LOG.warning("Could not update transfer progress; keeping the installation running.")


async def install(rsd, path, existing=None, streams=1):
    from pymobiledevice3.services.afc import AfcService
    from pymobiledevice3.services.installation_proxy import InstallationProxyService

    identifier = bundle_id(path)
    command = (
        "Upgrade"
        if identifier in (existing if existing is not None else await lookup(rsd))
        else "Install"
    )
    remote = "/PublicStaging/ipa-manager-" + os.urandom(12).hex() + ".ipa"
    started = time.monotonic()
    timing = {
        "archive_bytes": path.stat().st_size,
        "uploaded_bytes": 0,
        "command": command,
        "verified": False,
    }
    timing["upload_streams"] = streams if timing["archive_bytes"] >= 32 * 1024 * 1024 else 1
    if path.stem.startswith("remote-") and path.stem[7:].isdigit():
        timing["account"] = int(path.stem.split("-")[-1])
    async with AfcService(rsd) as afc:
        await afc.makedirs("/PublicStaging")
        try:
            upload_started = time.monotonic()
            timing["upload_started_at"] = time.time()
            transfer_progress(path, timing, "upload")

            def progress(count):
                timing["uploaded_bytes"] += count
                transfer_progress(path, timing, "upload")

            if timing["upload_streams"] == 2:
                from iphone_detector.afc_upload import upload

                await upload(rsd, afc, path, remote, progress)
                timing["upload_hash_verified"] = True
            else:
                handle = await afc.fopen(remote, "w")
                try:
                    with path.open("rb") as file:
                        while chunk := file.read(UPLOAD_CHUNK_SIZE):
                            await afc.fwrite(handle, chunk)
                            progress(len(chunk))
                finally:
                    await afc.fclose(handle)
            timing["upload_seconds"] = time.monotonic() - upload_started
            install_started = time.monotonic()
            timing["installation_started_at"] = time.time()
            transfer_progress(path, timing, "install")
            async with InstallationProxyService(rsd) as installer:
                await installer.send_package(command, {"PackageType": "Developer"}, None, remote)
            timing["installation_seconds"] = time.monotonic() - install_started
            verify_started = time.monotonic()
            transfer_progress(path, timing, "verify")
            if identifier not in await lookup(rsd):
                raise RuntimeError("Installed app was not found during verification.")
            timing.update(verified=True, verification_seconds=time.monotonic() - verify_started)
            transfer_progress(path, timing, "completed")
        finally:
            try:
                await afc.rm_single(remote, force=True)
            except Exception:
                LOG.warning("Could not remove owned staging archive.")
            timing["total_seconds"] = time.monotonic() - started
            timing["finished_at"] = time.time()
            try:
                with (path.parent / "transfer-metrics.jsonl").open(
                    "a", encoding="utf-8"
                ) as metrics:
                    metrics.write(json.dumps(timing) + "\n")
            except OSError:
                LOG.warning("Could not write transfer timing metrics.")


def job_accounts(job):
    """Validate the explicit account selection attached to a queued job."""
    try:
        chosen = json.loads((job / "remote-request.json").read_text(encoding="utf-8")).get(
            "accounts"
        )
    except FileNotFoundError:
        raise RuntimeError("Remote request has no account selection.")
    except (OSError, ValueError, AttributeError) as exc:
        raise RuntimeError("Remote request is missing or unreadable.") from exc
    if chosen is None:
        raise RuntimeError("Remote request has no account selection.")
    if not isinstance(chosen, list) or not chosen:
        raise RuntimeError("Remote request contains an invalid account selection.")
    numbers = []
    for value in chosen:
        if type(value) is not int or not 1 <= value <= 25 or value in numbers:
            raise RuntimeError("Remote request contains an invalid account selection.")
        numbers.append(value)
    return sorted(numbers)


async def execute_job(job, rsd):
    from iphone_detector.background import BackgroundStop, make_job, run_background
    from iphone_detector.config import load_config

    stopped = threading.Event()
    native = None
    outcome = None
    completed = 0
    refreshed = 0
    installed_accounts = []
    try:
        # Verify this is still the configured phone before accessing Apple accounts.
        expected = (load_config() or {}).get("device", {}).get("serial", "")
        if canonical_serial(rsd.udid) != canonical_serial(expected):
            raise RuntimeError("Tunnel identity does not match the configured iPhone.")
        numbers = job_accounts(job)
        request = make_job(
            load_config() or {}, "Remote", accounts=[number - 1 for number in numbers]
        )
        from iphone_detector.ipa_assignments import needs_install

        config_snapshot = load_config() or {}
        pending_installs = {
            entry["account"]
            for entry in request.get("accounts", [])
            if needs_install(config_snapshot, entry["account"], Path(entry["ipa"]).stem)
        }
        request.update(serial=rsd.udid, device_name=rsd.name or "iPhone")
        refresh_requested = (job / "refresh-request").exists()
        request["profile_refresh"] = refresh_requested
        from iphone_detector.profile_refresh import Inventory

        inventory = Inventory()
        request["remote_compression"] = await asyncio.to_thread(compression_level)
        streams = await asyncio.to_thread(upload_streams)
        existing = await asyncio.wait_for(lookup(rsd), 20)
        native = asyncio.create_task(
            asyncio.to_thread(
                run_background, job, BackgroundStop(stopped), "Remote", False, request
            )
        )
        for archive in numbers:
            ready = job / f"remote-{archive}.ready"
            sign_deadline = time.monotonic() + request["timeout"]
            while not ready.exists():
                if native.done():
                    await native
                    raise RuntimeError("Signing stopped before all accounts completed.")
                if cancelled(job):
                    raise RuntimeError(
                        "Remote installation cancelled. Inspect phone before retrying."
                    )
                if time.monotonic() > sign_deadline:
                    raise RuntimeError("Remote signing timed out. Inspect phone before retrying.")
                await asyncio.sleep(0.2)

            async def transfer_account():
                nonlocal refreshed
                path = job / f"remote-{archive}.ipa"
                if refresh_requested:
                    from iphone_detector.profile_refresh import refresh

                    if archive not in pending_installs and await refresh(rsd, path, inventory):
                        refreshed += 1
                        return
                    LOG.info(
                        "Account %s requires full installation; using the saved IPA after profile eligibility or verification checks.",
                        archive,
                    )
                    await require_full_archive(job, archive, native)
                    inventory.invalidate()
                await install(rsd, path, existing, streams)
                installed_accounts.append(archive)

            transfer = asyncio.create_task(transfer_account())
            try:
                deadline = time.monotonic() + request["timeout"]
                while not transfer.done():
                    if cancelled(job) or time.monotonic() > deadline:
                        raise RuntimeError(
                            "Remote installation stopped. Inspect phone before retrying."
                        )
                    await asyncio.sleep(0.2)
                await transfer
                from iphone_detector.operations import record

                record(
                    job, request, archive, "install" if archive in installed_accounts else "refresh"
                )
            finally:
                if not transfer.done():
                    transfer.cancel()
                    await asyncio.gather(transfer, return_exceptions=True)
            (job / f"remote-{archive}.installed").touch()
            completed += 1
        data = await native
        outcome = (
            dict(
                data,
                refreshed_accounts=refreshed,
                accounts=numbers,
                installed_accounts=installed_accounts,
            )
            if refresh_requested
            else dict(data, accounts=numbers, installed_accounts=installed_accounts)
        )
    except asyncio.CancelledError:
        outcome = {
            "ok": False,
            "error": "Remote connection stopped during installation. Inspect phone before retrying.",
        }
    except Exception as exc:
        LOG.exception("Remote job stopped")
        # Use sanitized native summary only; never expose transport or credential dumps.
        outcome = {
            "ok": False,
            "error": str(exc)[:900]
            if isinstance(exc, RuntimeError)
            else "Remote installation failed. Check the local remote-tunnel log; inspect phone before retrying.",
        }
    finally:
        stopped.set()
        if native:
            await asyncio.gather(native, return_exceptions=True)
        for path in job.glob("remote-*.ipa"):
            path.unlink(missing_ok=True)
        if outcome is not None:
            outcome["verified_accounts"] = numbers[:completed] if "numbers" in locals() else []
            if not outcome.get("ok"):
                outcome["completed_accounts"] = completed
            atomic(job / "remote-result.json", outcome)


async def require_full_archive(job, index, native):
    """Never pass a metadata-only renewal bundle to InstallationProxy."""
    if not (job / f"remote-{index}.profiles").exists():
        return
    (job / f"remote-{index}.full-install").touch()
    while not (job / f"remote-{index}.full-ready").exists():
        if native.done():
            await native
            raise RuntimeError("Full IPA preparation stopped before installation.")
        if cancelled(job):
            raise RuntimeError("Full IPA preparation cancelled.")
        await asyncio.sleep(0.2)


def cancelled(job):
    try:
        return (job / "stop").exists() or time.time() - (
            job / "remote-heartbeat"
        ).stat().st_mtime > 15
    except OSError:
        return True


def pending():
    root = DATA_ROOT / "automation-runs"
    for request in sorted(root.glob("*/remote-request.json")):
        job = request.parent
        if (job / "remote-result.json").exists():
            continue
        if cancelled(job):
            atomic(
                job / "remote-result.json",
                {"ok": False, "error": "Remote request expired or was cancelled."},
            )
            continue
        yield job


async def connected(config, publish):
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from iphone_detector.remote_check import select_peer

    binary = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"
    status = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            [str(binary), "status", "--json"],
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    )
    ip = select_peer(status, config["ip"])
    tunnel_service.TIMEOUT = 8
    tunnel_service.USE_USERSPACE_TUNNEL = True
    async with AsyncExitStack() as stack:
        remote = await asyncio.wait_for(
            tunnel_service.create_core_device_tunnel_service_using_remotepairing(
                config["serial"], ip, config["port"], autopair=False
            ),
            20,
        )
        stack.push_async_callback(remote.close)
        tunnel = await asyncio.wait_for(stack.enter_async_context(remote.start_tcp_tunnel()), 20)
        tunnel.client.tun.set_peer(tunnel.address)
        plane = await stack.enter_async_context(
            UserspaceDialPlane(tunnel.client.tun, tunnel.address)
        )
        rsd = RemoteServiceDiscoveryService(
            (tunnel.address, tunnel.port),
            open_connection=plane.dial,
            auxiliary_metadata=tunnel.auxiliary_metadata,
        )
        stack.push_async_callback(rsd.close)
        await asyncio.wait_for(rsd.connect(), 20)
        if canonical_serial(rsd.udid) != canonical_serial(config["serial"]):
            raise RuntimeError("Remote device identity mismatch")
        await asyncio.wait_for(lookup(rsd), 20)
        LOG.info("Persistent authenticated tunnel ready.")
        last_check = time.monotonic()
        failures = 0
        while enabled() and not (DATA_ROOT / "remote-stop").exists():
            publish("ready" if failures == 0 else "connecting")
            for job in pending():
                publish("installing")
                operation = asyncio.create_task(execute_job(job, rsd))
                try:
                    while not operation.done():
                        publish("installing")
                        await asyncio.sleep(1)
                    await operation
                finally:
                    if not operation.done():
                        operation.cancel()
                        await asyncio.gather(operation, return_exceptions=True)
                last_check = 0
            if time.monotonic() - last_check >= 15:
                try:
                    await asyncio.wait_for(lookup(rsd), 12)
                    failures = 0
                except Exception:
                    failures += 1
                    publish("connecting")
                    if failures >= 3:
                        raise RuntimeError("Persistent connection lost")
                last_check = time.monotonic()
            await asyncio.sleep(1)


async def daemon():
    from iphone_detector.run_lock import RunLock

    lock = RunLock()
    lock.name = "Local\\iPhoneIPAManagerRemoteTunnel"
    if not lock.acquire():
        return
    (DATA_ROOT / "remote-stop").unlink(missing_ok=True)

    def publish(value):
        try:
            atomic(STATE, {"state": value, "updated": time.time(), "pid": os.getpid()})
        except OSError:
            # Reporting failure must never destroy an authenticated roaming tunnel.
            LOG.warning("Could not update tunnel status; keeping the connection open.")

    try:
        while enabled() and not (DATA_ROOT / "remote-stop").exists():
            publish("connecting")
            try:
                await connected(settings(), publish)
            except Exception as exc:
                LOG.warning(
                    "Tunnel unavailable (%s); will retry without pairing or installing.",
                    type(exc).__name__,
                )
                # Bonjour is available at home; learn a changed listener port there.
                try:
                    from pymobiledevice3.remote.tunnel_service import (
                        get_remote_pairing_tunnel_services,
                    )

                    current = settings()
                    listeners = await asyncio.wait_for(
                        get_remote_pairing_tunnel_services(
                            bonjour_timeout=2, udid=current["serial"]
                        ),
                        10,
                    )
                    for listener in listeners:
                        if canonical_serial(listener.remote_identifier) == canonical_serial(
                            current["serial"]
                        ):
                            current["port"] = listener.port
                            atomic(SETTINGS, current)
                        await listener.close()
                except Exception:
                    pass
                for _ in range(15):
                    publish("waiting")
                    if not enabled() or (DATA_ROOT / "remote-stop").exists():
                        return
                    await asyncio.sleep(1)
    finally:
        publish("offline")
        lock.close()


def run():
    from iphone_detector.logger import setup_logging

    setup_logging(log_file=str(DATA_ROOT / "remote-tunnel.log"))
    asyncio.run(daemon())
