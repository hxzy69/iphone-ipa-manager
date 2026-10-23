"""Shared desktop/Discord overview; no speculative device or expiry claims."""

from datetime import datetime
import math
from pathlib import Path
import time

from iphone_detector.paths import DATA_ROOT
from iphone_detector.config import load_config
from iphone_detector.settings import get_workflow
from iphone_detector.job_history import read
from iphone_detector.operations import account_status, jobs


def date(value):
    try:
        return datetime.fromtimestamp(value).astimezone().strftime("%d %b %H:%M")
    except TypeError, ValueError, OSError:
        return "Unknown"


def connection(root=DATA_ROOT):
    from iphone_detector import remote_tunnel

    state = remote_tunnel.state()["state"]
    route = "No recent installation"
    speed = None
    for job in jobs(root):
        transport = read(job / "route.json").get("transport")
        if transport in ("local-wifi", "remote"):
            route = "Last route: " + (
                "Local Wi-Fi" if transport == "local-wifi" else "Remote tunnel"
            )
            metrics = read(job / "remote-transfer.json")
            try:
                elapsed = metrics["updated_at"] - metrics["upload_started_at"]
                uploaded = metrics["uploaded_bytes"]
                if (
                    metrics["stage"] == "upload"
                    and -5 <= time.time() - metrics["updated_at"] < 15
                    and type(uploaded) is int
                    and uploaded >= 0
                    and elapsed > 0
                ):
                    value = uploaded / elapsed / (1024 * 1024)
                    if math.isfinite(value):
                        speed = round(value, 2)
            except KeyError, TypeError, ValueError:
                pass
            break
    reasons = {
        "ready": "Authenticated remote tunnel available.",
        "offline": "No live remote tunnel. Check that the PC and phone are online; home Wi-Fi may be needed to re-establish it.",
        "waiting": "Waiting for the phone. Check Tailscale and reconnect on home Wi-Fi if needed.",
        "connecting": "Pairing transport is connecting; no readiness is assumed yet.",
        "installing": "The tunnel is handling an installation.",
    }
    return {
        "tunnel": state,
        "route": route,
        "upload_mib_s": speed,
        "explanation": reasons.get(state, "Connection state unavailable."),
    }


def snapshot():
    from iphone_detector import auto_refresh, job_queue

    config = load_config() or {}
    folder = Path(config.get("automation", {}).get("ipa_directory", Path.home() / "Downloads/ipas"))
    outcomes = account_status(DATA_ROOT, config)
    accounts = []
    for index, account in enumerate(get_workflow(config), 1):
        receipt = outcomes.get(index, {})
        expiry = receipt.get("profile_expires_at")
        accounts.append(
            {
                "number": index,
                "email": account["email"],
                "ipa": account["ipa"],
                "file_exists": (folder / (account["ipa"] + ".ipa")).is_file(),
                "last_verified": date(receipt.get("at")),
                "operation": receipt.get("operation", "Unverified"),
                "profile_expiry": date(expiry),
                "profile_expired": isinstance(expiry, (int, float)) and expiry <= time.time(),
            }
        )
    return {
        "accounts": accounts,
        "connection": connection(),
        "renewal": auto_refresh.describe(),
        "queue": job_queue.summary(),
    }
