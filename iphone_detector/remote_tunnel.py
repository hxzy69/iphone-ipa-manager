"""Persistent, user-level RemotePairing transport and serialized install queue."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
import plistlib
import zipfile

from iphone_detector.paths import DATA_ROOT, FROZEN, profile_lock_name
from iphone_detector.storage import finite_number
from iphone_detector.wifi import canonical_serial

LOG = logging.getLogger("iphone-detector.remote")
SETTINGS = DATA_ROOT / "remote.json"
STATE = DATA_ROOT / "remote-state.json"
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def atomic(path, value):
    from iphone_detector.storage import write_json

    write_json(path, value)


def settings():
    value = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    if not isinstance(value, dict) or ("enabled" in value and type(value["enabled"]) is not bool):
        raise ValueError("Remote settings need local repair; existing settings were preserved.")
    if value.get("enabled") and (
        not isinstance(value.get("serial"), str)
        or not value["serial"]
        or not isinstance(value.get("ip"), str)
        or not value["ip"]
        or type(value.get("port")) is not int
        or not 1 <= value["port"] <= 65535
    ):
        raise ValueError("Remote device settings are incomplete. Repeat local remote setup.")
    return value


def enabled():
    return bool(settings().get("enabled"))


def state():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if (
            isinstance(data, dict)
            and data.get("state") in ("ready", "installing", "waiting", "connecting", "offline")
            and finite_number(data.get("updated"))
            and -5 <= time.time() - data["updated"] < 45
        ):
            return data
    except PermissionError:
        return {"state": "connecting"}
    except OSError, ValueError, KeyError:
        pass
    return {"state": "offline"}


def describe():
    return describe_state(state()["state"])


def describe_state(value):
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
    if FROZEN:
        from iphone_detector.live_status import query

        live = query(DATA_ROOT)
        if live is not None and live["tunnel"] != "offline":
            return
    from iphone_detector.discord_bot import command
    from iphone_detector.startup_task import start_existing

    if FROZEN and start_existing(
        command(["--remote-daemon"]), name="iPhone IPA Community Remote Tunnel"
    ):
        return

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
    offline_since = None
    deadline = time.monotonic() + 60 + 2 * request["timeout"] * len(selected)
    try:
        stop.check()
        (job / "remote-heartbeat").touch()
        atomic(job / "remote-request.json", {"created": time.time(), "accounts": selected})
        while not (job / "remote-result.json").exists():
            stop.check()
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Remote request timed out. Inspect /history and the phone before retrying."
                )
            (job / "remote-heartbeat").touch()
            if state()["state"] not in ("ready", "installing"):
                offline_since = offline_since or time.monotonic()
                if time.monotonic() - offline_since > 30:
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
    return completed_result(result, len(selected))


def completed_result(result, expected):
    """Only a typed, final completion can advance a request or its renewal timer."""
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or type(result.get("completed_accounts")) is not int
        or result["completed_accounts"] != expected
        or result.get("prepared_only")
    ):
        message = result.get("error") if isinstance(result, dict) else None
        raise RuntimeError(
            message
            if isinstance(message, str) and message
            else "Remote installation did not complete. Inspect the phone before retrying."
        )
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
            verified_apps = await lookup(rsd)
            if identifier not in verified_apps:
                raise RuntimeError("Installed app was not found during verification.")
            if existing is not None:
                # Keep the job's app inventory current for subsequent accounts.
                # A later archive for the same bundle must use Upgrade, not Install.
                verified_apps = dict(verified_apps)
                existing.clear()
                existing.update(verified_apps)
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
    claimed = False
    if (job / "remote-result.json").exists():
        return
    try:
        # Durable claim precedes signing/device access. Recovery never retries an
        # attempt whose process died before its final receipt became durable.
        with (job / "remote-execution.json").open("x", encoding="utf-8") as claim:
            claimed = True
            json.dump({"started_at": time.time(), "pid": os.getpid()}, claim)
            claim.flush()
            os.fsync(claim.fileno())
        # Verify this is still the configured phone before accessing Apple accounts.
        expected = (load_config() or {}).get("device", {}).get("serial", "")
        if canonical_serial(rsd.udid) != canonical_serial(expected):
            raise RuntimeError("Tunnel identity does not match the configured iPhone.")
        numbers = job_accounts(job)
        request = make_job(
            load_config() or {}, "Remote", accounts=[number - 1 for number in numbers]
        )
        selection = job / "selection.json"
        if selection.exists():
            from iphone_detector.operations import selection_matches

            if not selection_matches(json.loads(selection.read_text(encoding="utf-8")), request):
                raise RuntimeError(
                    "The selected account or IPA changed before remote dispatch. Start a new request."
                )
        from iphone_detector.ipa_assignments import needs_install

        config_snapshot = load_config() or {}
        pending_installs = {
            entry["account"]
            for entry in request.get("accounts", [])
            if needs_install(config_snapshot, entry["account"], Path(entry["ipa"]).stem)
        }
        request.update(
            serial=rsd.udid,
            device_name=getattr(rsd, "name", None)
            or getattr(rsd, "display_name", None)
            or "iPhone",
        )
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
        if outcome is not None:
            outcome["verified_accounts"] = numbers[:completed] if "numbers" in locals() else []
            if not outcome.get("ok"):
                outcome["completed_accounts"] = completed
            atomic(job / "remote-result.json", outcome)
        for path in job.glob("remote-*.ipa") if claimed else ():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                LOG.warning("Could not remove a temporary signed IPA; final result was preserved.")


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


def pending(data_root=None):
    root = (DATA_ROOT if data_root is None else Path(data_root)) / "automation-runs"
    for request in sorted(root.glob("*/remote-request.json")):
        job = request.parent
        try:
            if json.loads(request.read_text(encoding="utf-8")).get("execution") == "local":
                continue
        except OSError, ValueError, AttributeError:
            continue
        if (job / "remote-result.json").exists():
            continue
        if any(
            (job / name).exists()
            for name in (
                "remote-execution.json",
                "native-dispatch.json",
                "native-input.json",
                "backend-job.json",
                "backend-events.jsonl",
            )
        ):
            atomic(
                job / "remote-result.json",
                {
                    "ok": False,
                    "error": "Remote worker was interrupted after dispatch. Inspect /history and the phone before retrying; this request was not replayed.",
                },
            )
            continue
        if cancelled(job):
            atomic(
                job / "remote-result.json",
                {"ok": False, "error": "Remote request expired or was cancelled."},
            )
            continue
        yield job


@asynccontextmanager
async def open_session(config, address):
    """Authenticate a userspace tunnel and its installation service before yielding."""
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from iphone_detector.remote_check import pairing_identifier

    tunnel_service.TIMEOUT = 8
    tunnel_service.USE_USERSPACE_TUNNEL = True
    async with AsyncExitStack() as stack:
        remote = await asyncio.wait_for(
            tunnel_service.create_core_device_tunnel_service_using_remotepairing(
                pairing_identifier(config["serial"]), address, config["port"], autopair=False
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
        watch = asyncio.create_task(tunnel.client.wait_closed())

        async def stop_watch():
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)

        stack.push_async_callback(stop_watch)
        rsd._ipa_transport_watch = watch
        yield rsd


async def connected(config, publish):
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
    async with open_session(config, ip) as rsd:
        LOG.info("Persistent authenticated tunnel ready.")
        last_check = time.monotonic()
        failures = 0
        while enabled() and not (DATA_ROOT / "remote-stop").exists():
            if rsd._ipa_transport_watch.done():
                raise ConnectionError("Remote tunnel transport closed")
            publish("ready" if failures == 0 else "connecting")
            for job in pending() if failures == 0 else ():
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
                    if failures == 3:
                        LOG.warning(
                            "Installation service unavailable; retaining the open roaming transport."
                        )
                last_check = time.monotonic()
            await asyncio.sleep(1)


async def daemon(status_channel=None):
    from iphone_detector.run_lock import RunLock

    lock = RunLock()
    lock.name = profile_lock_name("RemoteTunnel")
    if not lock.acquire():
        return
    (DATA_ROOT / "remote-stop").unlink(missing_ok=True)
    from iphone_detector.activation_health import ready

    settings()  # Reject unreadable configuration before acknowledging startup.
    ready("remote")

    def publish(value):
        if status_channel is not None:
            status_channel.update(value)
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
                    from iphone_detector.remote_discovery import discover

                    current = settings()
                    listeners = await discover(current["serial"], current)
                    for listener in listeners:
                        if canonical_serial(listener.remote_identifier) == canonical_serial(
                            current["serial"]
                        ):
                            current["port"] = listener.port
                            current["serial"] = listener.remote_identifier
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
    from iphone_detector.live_status import StatusServer
    from iphone_detector.auto_refresh import describe as renewal_summary

    setup_logging(log_file="remote-tunnel.log")
    with StatusServer(DATA_ROOT, renewal_summary) as status_channel:
        asyncio.run(daemon(status_channel))
