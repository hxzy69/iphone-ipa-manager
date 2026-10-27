"""Standalone GUI entry point and isolated background worker modes."""

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path

# Use the reviewed GPL transport sources supplied with this distribution.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor/python"))


def worker(mode, job, parent_pid=None, accounts=None):
    from iphone_detector.logger import setup_logging

    job.mkdir(parents=True, exist_ok=True)
    setup_logging(log_file=str(job / "activity.log"))
    log = logging.getLogger("iphone-detector")
    stopped = threading.Event()
    finished = threading.Event()
    from iphone_detector.config import load_config

    try:
        config = load_config() or {}
    except ValueError as exc:
        from iphone_detector.storage import write_json

        write_json(job / "result.json", {"error": str(exc), "error_code": "CONFIG_UNREADABLE"})
        return 1
    from iphone_detector.remote_tunnel import enabled as remote_enabled

    try:
        remote_configured = remote_enabled() if mode in ("discord-run", "wifi-run") else False
        tunnel_run = mode in ("discord-run", "discord-refresh") or (
            mode == "wifi-run" and remote_configured
        )
    except OSError, ValueError, TypeError:
        from iphone_detector.storage import write_json

        write_json(
            job / "result.json",
            {
                "error": "Remote settings need repair before starting this operation.",
                "error_code": "REMOTE_SETTINGS_UNREADABLE",
            },
        )
        return 1
    remote = mode in ("discord-run", "discord-refresh")
    if mode == "discord-refresh" and accounts:
        (job / "result.json").write_text(
            json.dumps({"error": "Profile refresh always renews all configured installed apps."})
        )
        return 1
    if remote and not tunnel_run:
        mode = "wifi-run"
        from iphone_detector.bridge_service import installed

        if not installed():
            (job / "result.json").write_text(
                json.dumps(
                    {
                        "error": "Install the Wi-Fi service locally first. Discord cannot request administrator approval."
                    }
                )
            )
            return 1
    succeeded = False
    failure = None
    from iphone_detector.run_lock import RunLock

    lock = RunLock() if tunnel_run or mode in ("run", "watch", "wifi-run", "wifi-check") else None
    if lock is not None and not lock.acquire():
        (job / "result.json").write_text(
            json.dumps({"error": "Another installation or connection check is already running."})
        )
        return 1
    if remote:
        try:
            from iphone_detector.background import validate_selection

            validate_selection(job, load_config() or {}, accounts)
        except (OSError, ValueError, TypeError) as exc:
            from iphone_detector.storage import write_json

            write_json(job / "result.json", {"error": str(exc)})
            if lock is not None:
                lock.close()
            return 1
    parent = None
    if parent_pid:
        from iphone_detector.wifi import process_handle

        try:
            parent = process_handle(parent_pid)
        except RuntimeError:
            stopped.set()

    def monitor():
        while not finished.wait(0.1):
            if (job / "stop").exists() or (
                parent and parent[0].WaitForSingleObject(parent[1], 0) != 258
            ):
                stopped.set()
                return

    threading.Thread(target=monitor, daemon=True, name="stop-request").start()

    completed_data = None

    def result(data):
        nonlocal completed_data
        from iphone_detector.storage import write_json
        from iphone_detector.operations import collect_native

        write_json(job / "result.json", data)
        if data.get("ok") is True:
            completed_data = data
        try:
            collect_native(job)
        except OSError, ValueError, TypeError, KeyError:
            log.warning(
                "Could not collect account completion metadata; the final operation receipt was preserved."
            )
        try:
            from iphone_detector.ipa_assignments import acknowledge_installs

            acknowledge_installs(job, data)
        except OSError, ValueError, KeyError, TypeError:
            log.warning(
                "Could not acknowledge the new IPA assignment; it remains pending for the next run."
            )
        try:
            from iphone_detector.auto_refresh import record_success

            record_success(job, data)
        except OSError, ValueError, TypeError, KeyError:
            log.warning("Could not update the automatic renewal schedule.")

    try:
        from iphone_detector.worker_registry import register

        register(job, mode, accounts)
        log.info("Starting %s", mode)
        if tunnel_run:
            from iphone_detector.background import BackgroundStop

            if mode == "discord-refresh":
                from iphone_detector.connection_router import run_refresh

                result(run_refresh(job, BackgroundStop(stopped)))
            else:
                from iphone_detector.connection_router import run_automatic

                result(run_automatic(job, BackgroundStop(stopped), accounts))
            succeeded = True
        elif mode in ("wifi-check", "wifi-run"):
            from iphone_detector.wifi import WifiBridge
            from iphone_detector.background import BackgroundStop, run_background

            serial = config.get("device", {}).get("serial")
            if not serial:
                raise ValueError("Connect and select your iPhone by USB first.")
            control = BackgroundStop(stopped)
            with WifiBridge(job, serial, control, allow_elevation=False):
                result(
                    run_background(
                        job, control, "Network", check_only=mode == "wifi-check", accounts=accounts
                    )
                )
            succeeded = mode == "wifi-run"
        elif mode == "notification-check":
            from iphone_detector.notify import notify

            result(notify("iPhone IPA Manager", "Background completion notifications are ready."))
        elif mode == "devices":
            from iphone_detector.detector import is_mobile_device, list_apple_devices

            devices = {}
            for device in list_apple_devices():
                if is_mobile_device(device) and device.serial:
                    devices.setdefault(
                        device.serial,
                        {"serial": device.serial, "name": device.name, "pid": device.pid},
                    )
            result({"devices": list(devices.values())})
            log.info("Found %d Apple mobile device(s)", len(devices))
        elif mode == "check":
            from iphone_detector.setup import preflight

            result(preflight(config))
            log.info("Saved configuration, account assignments and IPA archives passed validation.")
        elif mode == "run":
            from iphone_detector.background import BackgroundStop, run_background

            result(run_background(job, BackgroundStop(stopped), accounts=accounts))
            succeeded = True
        elif mode == "watch":
            from iphone_detector.config import load_config
            from iphone_detector.detector import IPhoneWatcher
            from iphone_detector.events import EventDispatcher
            from iphone_detector.cancellation import Cancelled

            serial = (load_config() or {}).get("device", {}).get("serial")
            if not serial:
                raise ValueError("Pair an iPhone before starting USB watch.")
            dispatcher = EventDispatcher()
            outcome = [130]

            def connected(payload):
                nonlocal succeeded, failure
                if stopped.is_set():
                    return
                try:
                    log.info("Paired iPhone connected. Starting the configured accounts.")
                    from iphone_detector.background import BackgroundStop, run_background

                    result(run_background(job, BackgroundStop(stopped)))
                    succeeded = True
                    outcome[0] = 0
                except Cancelled:
                    log.info("Installation cancelled.")
                    result({"cancelled": True})
                    outcome[0] = 130
                except Exception as exc:
                    failure = str(exc)
                    result({"error": str(exc)})
                    log.exception("Installation failed. Inspect the activity log before retrying.")
                    outcome[0] = 1
                finally:
                    stopped.set()

            dispatcher.on("connect", connected)
            dispatcher.on("disconnect", lambda payload: log.info("iPhone disconnected."))
            log.info("Waiting for paired iPhone. Stop ends USB watch.")
            IPhoneWatcher(dispatcher, target_serial=serial, emit_startup=True).run_forever(
                stop_event=stopped
            )
            return outcome[0]
        if stopped.is_set():
            return 130
        return 0
    except Exception as exc:
        from iphone_detector.cancellation import Cancelled
        from iphone_detector.wifi import BridgeCleanupError

        if isinstance(exc, BridgeCleanupError):
            completed = exc.completed_result or completed_data
            if completed and completed.get("ok") is True:
                failure = (
                    "Installation completed, but Wi-Fi bridge cleanup needs attention. " + str(exc)
                )
                log.exception("%s", failure)
                if completed_data is None:
                    result(completed)
                from iphone_detector.storage import write_json

                write_json(
                    job / "result.json", dict(completed, cleanup_warning=failure, error=failure)
                )
                return 1

        if isinstance(exc, Cancelled):
            log.info("Installation cancelled safely.")
            result({"cancelled": True})
            return 130
        failure = str(exc)
        log.exception("%s", exc)
        result({"error": str(exc)})
        return 1
    finally:
        finished.set()
        try:
            from iphone_detector.worker_registry import finish

            finish(job)
        except OSError:
            log.warning(
                "Could not finalize worker registry; process identity still prevents stale cancellation."
            )
        if parent:
            parent[0].CloseHandle(parent[1])
        if lock:
            lock.close()
        if mode in (
            "run",
            "wifi-run",
            "discord-run",
            "discord-refresh",
            "watch",
        ) and not job.name.startswith("automatic-"):
            try:
                from iphone_detector.paths import DATA_ROOT
                from iphone_detector.discord_outbox import enqueue

                if job.resolve().parent == (DATA_ROOT / "automation-runs").resolve():
                    receipt = json.loads((job / "result.json").read_text(encoding="utf-8"))
                    from iphone_detector.settings import get_workflow

                    expected = len(set(accounts)) if accounts else len(get_workflow(config))
                    verified = (
                        receipt.get("ok") is True
                        and type(receipt.get("completed_accounts")) is int
                        and receipt["completed_accounts"] == expected
                        and not receipt.get("prepared_only")
                    )
                    scope = (
                        ", ".join(str(value + 1) for value in accounts)
                        if accounts
                        else ", ".join(str(value + 1) for value in range(expected))
                    )
                    message = (
                        "Run completed for accounts %s. Use /history for details." % scope
                        if verified
                        else "Run cancelled. Use /history for details."
                        if receipt.get("cancelled")
                        else "Run needs attention; no verified completion for the requested accounts. Inspect /history before retrying."
                    )
                    enqueue(DATA_ROOT, "job:" + str(job), message)
            except OSError, ValueError, TypeError, AttributeError:
                log.warning(
                    "Could not queue the durable completion report; the local receipt remains available."
                )
        if (tunnel_run or mode in ("run", "watch", "wifi-run")) and config.get(
            "automation", {}
        ).get("notify_on_finish", True):
            if succeeded or failure:
                try:
                    from iphone_detector.notify import notify

                    success_text = (
                        "Account refresh completed. See Discord for details."
                        if mode == "discord-refresh"
                        else "The requested app was installed successfully."
                        if accounts
                        else "All configured apps installed successfully."
                    )
                    notification = notify(
                        "iPhone IPA Manager — " + ("Complete" if succeeded else "Needs attention"),
                        success_text
                        if succeeded
                        else "Installation stopped. Open iPhone IPA Manager for details.",
                    )
                    (job / "notification.json").write_text(json.dumps(notification))
                    log.info("Windows notification submitted (%s).", notification.get("setting"))
                except Exception:
                    log.exception("Could not submit Windows notification")
        logging.shutdown()


def main():
    parser = argparse.ArgumentParser(description="iPhone IPA Manager")
    parser.add_argument(
        "--worker",
        choices=[
            "run",
            "watch",
            "check",
            "devices",
            "wifi-check",
            "wifi-run",
            "wifi-admin",
            "notification-check",
            "discord-run",
            "discord-refresh",
        ],
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--discord", action="store_true", help="Run the configured private Discord bot"
    )
    parser.add_argument("--remote-daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--queue-runner", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--activate-update", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--desktop-setup", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--desktop-uninstall", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--auto-refresh", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--enable-auto-refresh", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--discord-setup", action="store_true", help="Configure private Discord control"
    )
    parser.add_argument("--install-bridge", metavar="OWNER_SID", help=argparse.SUPPRESS)
    parser.add_argument("--job-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--account", type=int, choices=range(1, 26), action="append", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--self-test", action="store_true", help="Check this runtime without accessing the iPhone"
    )
    parser.add_argument("--report-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--setup-remote", action="store_true", help="Verify USB/Wi-Fi RemotePairing for Tailscale"
    )
    parser.add_argument(
        "--phone-ip", help="Optional iPhone Tailscale IPv4 address for remote setup"
    )
    parser.add_argument(
        "--lan-ip", help="Optional trusted LAN address instead of USB for remote setup"
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Use an explicit absolute private state directory"
    )
    args = parser.parse_args()
    if args.data_dir is not None:
        if not args.data_dir.is_absolute():
            parser.error("--data-dir must be an absolute folder path")
        os.environ["IPA_MANAGER_DATA_DIR"] = str(args.data_dir.resolve())
    if args.desktop_setup:
        from iphone_detector.desktop_install import setup_window

        setup_window(payload=args.desktop_setup)
        return 0
    if args.desktop_uninstall:
        from iphone_detector.desktop_install import launch_uninstaller

        launch_uninstaller(args.desktop_uninstall)
        return 0
    if args.setup_remote:
        from iphone_detector.remote_setup import configure

        configure(args.phone_ip, args.lan_ip)
        return 0
    if args.self_test:
        if args.report_file is None:
            parser.error("--self-test requires --report-file")
        from iphone_detector.self_test import run

        return run(args.report_file.resolve())
    if args.queue_runner:
        from iphone_detector.job_queue import run
        from iphone_detector.service_entry import run as run_service

        return run_service("queue", run)
    if args.activate_update:
        from iphone_detector.guided_update import activate

        return activate(args.activate_update, args.parent_pid)
    if args.enable_auto_refresh:
        from iphone_detector.auto_refresh import configure

        configure(True)
        return 0
    if args.auto_refresh:
        from iphone_detector.auto_refresh import run
        from iphone_detector.service_entry import run as run_service

        return run_service("renewal", run)
    if args.remote_daemon:
        from iphone_detector.remote_tunnel import run
        from iphone_detector.service_entry import run as run_service

        return run_service("remote", run)
    if args.install_bridge:
        from iphone_detector.paths import DATA_ROOT
        from iphone_detector.logger import setup_logging

        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        setup_logging(log_file=str(DATA_ROOT / "bridge-install.log"))
        try:
            from iphone_detector.bridge_service import install

            install(args.install_bridge)
            return 0
        except Exception:
            logging.getLogger("iphone-detector").exception("Wi-Fi service installation failed")
            return 1
    if args.discord_setup:
        from iphone_detector.discord_setup import launch

        launch()
        return 0
    if args.discord:
        from iphone_detector.discord_bot import run
        from iphone_detector.service_entry import run as run_service

        return run_service("discord", run)
    if args.worker:
        if args.job_dir is None:
            parser.error("--worker requires --job-dir")
        if args.worker == "wifi-admin":
            if not args.parent_pid:
                parser.error("Wi-Fi helper requires its controller process")
            from iphone_detector.wifi import supervise

            return supervise(args.job_dir.resolve(), args.parent_pid)
        if args.account and args.worker != "discord-run":
            parser.error("--account is only valid for a Discord installation worker")
        accounts = [number - 1 for number in args.account] if args.account else None
        return worker(args.worker, args.job_dir.resolve(), args.parent_pid, accounts)
    from iphone_detector.gui import launch

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
