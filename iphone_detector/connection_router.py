"""Prefer an authenticated, on-link iPhone; keep the remote tunnel available."""

import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import time

from iphone_detector.paths import DATA_ROOT
from iphone_detector.wifi import canonical_serial

LOG = logging.getLogger("iphone-detector.routing")
CACHE = DATA_ROOT / "local-iphone.json"
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


class LocalRefreshUnavailable(RuntimeError):
    """Read-only local preflight failed before any device operation was submitted."""


def find_usb(serial):
    from iphone_detector.wifi import devices

    try:
        matches = [
            row["Properties"]["SerialNumber"]
            for row in devices()
            if row.get("Properties", {}).get("ConnectionType") == "USB"
            and canonical_serial(row["Properties"].get("SerialNumber", ""))
            == canonical_serial(serial)
        ]
        return matches[0] if len(matches) == 1 else None
    except OSError, ValueError, KeyError, TypeError:
        return None


def route_ready(refresh=False):
    """Readiness means a live remote session or an authenticated local phone."""
    from iphone_detector import remote_tunnel
    from iphone_detector.config import load_config
    from iphone_detector.bridge_service import installed
    from iphone_detector.background import BackgroundStop
    import threading

    if remote_tunnel.enabled() and remote_tunnel.state()["state"] == "ready":
        return True
    config = load_config() or {}
    serial = config.get("device", {}).get("serial")
    if not serial:
        return False
    if find_usb(serial):
        # The worker checks paired identity and the installation service before signing.
        return True
    if not refresh and not installed() and not remote_tunnel.enabled():
        return False
    return bool(find_local(serial, remote_tunnel.settings(), BackgroundStop(threading.Event())))


def run_refresh(job, stop):
    """Reuse the verified renewal engine over a paired LAN session when available."""
    from iphone_detector import remote_tunnel
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config

    config = load_config() or {}
    request = make_job(config, "Remote")
    usb = find_usb(request["serial"])
    local = None if usb else find_local(request["serial"], remote_tunnel.settings(), stop)
    transport = "usb" if usb else "local-wifi"
    (job / "refresh-request").touch()
    if not usb and not local:
        remote_tunnel.atomic(job / "route.json", {"transport": "remote", "operation": "refresh"})
        return remote_tunnel.submit(job, stop)

    async def execute():
        from pymobiledevice3.lockdown import create_using_tcp, create_using_usbmux
        from pymobiledevice3.exceptions import PyMobileDevice3Exception
        from iphone_detector.remote_check import read_pair

        try:
            client = await asyncio.wait_for(
                create_using_usbmux(
                    serial=usb,
                    identifier=request["serial"],
                    pair_record=read_pair(request["serial"]),
                    autopair=False,
                    connection_type="USB",
                )
                if usb
                else create_using_tcp(
                    local,
                    identifier=request["serial"],
                    pair_record=read_pair(request["serial"]),
                    autopair=False,
                    keep_alive=True,
                ),
                15,
            )
        except (OSError, TimeoutError, PyMobileDevice3Exception) as exc:
            raise LocalRefreshUnavailable("Local renewal session is unavailable.") from exc
        try:
            if canonical_serial(client.udid) != canonical_serial(request["serial"]):
                raise RuntimeError("Local device identity changed. Renewal cancelled.")
            try:
                await asyncio.wait_for(remote_tunnel.lookup(client), 20)
            except (OSError, TimeoutError, PyMobileDevice3Exception) as exc:
                raise LocalRefreshUnavailable("Local installation service is unavailable.") from exc
            stop.check()
            remote_tunnel.atomic(
                job / "remote-request.json",
                {"accounts": [a["account"] for a in request["accounts"]], "execution": "local"},
            )
            remote_tunnel.atomic(
                job / "route.json", {"transport": transport, "operation": "refresh"}
            )
            # execute_job supplies signing, fallback, cancellation and verified receipts.
            operation = asyncio.create_task(remote_tunnel.execute_job(job, client))
            try:
                while not operation.done():
                    (job / "remote-heartbeat").touch()
                    if stop.cancelled.is_set():
                        (job / "stop").touch()
                    await asyncio.sleep(0.2)
                await operation
            finally:
                if not operation.done():
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
        finally:
            await client.close()

    try:
        asyncio.run(execute())
    except LocalRefreshUnavailable:
        stop.check()
        if local and remote_tunnel.enabled():
            try:
                return run_local_tunnel(job, stop, local, refresh=True)
            except LocalRefreshUnavailable:
                stop.check()
        LOG.info(
            "Local renewal preflight was unavailable; using the authenticated remote tunnel before submitting device work."
        )
        remote_tunnel.atomic(
            job / "route.json",
            {"transport": "remote", "operation": "refresh", "local_preflight_failed": True},
        )
        return remote_tunnel.submit(job, stop)
    result = json.loads((job / "remote-result.json").read_text(encoding="utf-8"))
    remote_tunnel.completed_result(result, len(request["accounts"]))
    return dict(result, transport=transport)


def local_networks():
    import ifaddr

    result = []
    for adapter in ifaddr.get_adapters():
        label = (adapter.name + " " + adapter.nice_name).lower()
        if any(
            word in label
            for word in ("tailscale", "wireguard", "zerotier", "vpn", "tunnel", "loopback")
        ):
            continue
        for address in adapter.ips:
            if isinstance(address.ip, str) and ":" not in address.ip:
                try:
                    network = ipaddress.ip_network(
                        (address.ip, address.network_prefix), strict=False
                    )
                except ValueError, TypeError:
                    continue
                if network.prefixlen < 31 and any(network.subnet_of(scope) for scope in PRIVATE):
                    result.append(network)
    return result


def on_link(ip, networks):
    try:
        address = ipaddress.ip_address(ip)
        return (
            address.version == 4
            and any(address in scope for scope in PRIVATE)
            and any(address in network for network in networks)
        )
    except ValueError:
        return False


def paired_local_session(ip, serial):
    from iphone_detector.remote_check import read_pair, tls_context, pinned
    from iphone_detector.wifi import lockdown

    pair = read_pair(serial)
    with socket.create_connection((ip, 62078), timeout=2) as raw:
        raw.settimeout(3)
        session = lockdown(
            raw,
            {"Request": "StartSession", "HostID": pair["HostID"], "SystemBUID": pair["SystemBUID"]},
        )
        if not session.get("EnableSessionSSL"):
            raise RuntimeError("Local device did not enable paired TLS.")
        secure = pinned(tls_context(pair), raw, pair)
        try:
            identity = lockdown(secure, {"Request": "GetValue", "Key": "UniqueDeviceID"})["Value"]
            if canonical_serial(identity) != canonical_serial(serial):
                raise RuntimeError("Local device identity does not match the saved iPhone.")
            lockdown(secure, {"Request": "StopSession", "SessionID": session["SessionID"]})
        finally:
            secure.close()


def tailscale_candidate(config):
    if not config.get("ip"):
        return None
    from iphone_detector.remote_check import select_peer

    binary = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"
    status = json.loads(
        subprocess.check_output(
            [str(binary), "status", "--json"], timeout=4, creationflags=subprocess.CREATE_NO_WINDOW
        )
    )
    selected = select_peer(status, config["ip"])
    peer = next(
        p
        for p in status["Peer"].values()
        if isinstance(p, dict)
        and isinstance(p.get("OS"), str)
        and p["OS"].lower() == "ios"
        and isinstance(p.get("TailscaleIPs"), list)
        and selected in p["TailscaleIPs"]
    )
    endpoint = peer.get("CurAddr") or ""
    if not isinstance(endpoint, str):
        return None
    if endpoint.startswith("[") and "]:" in endpoint:
        return endpoint[1:].rsplit("]:", 1)[0]
    return endpoint.rsplit(":", 1)[0] if endpoint.count(":") == 1 else None


def find_local(serial, config, stop):
    from iphone_detector.remote_tunnel import atomic

    networks = local_networks()
    candidates = []
    try:
        candidates.append(tailscale_candidate(config))
    except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
        pass
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("serial"), str)
            and canonical_serial(cached["serial"]) == canonical_serial(serial)
        ):
            candidates.append(cached.get("ip"))
    except OSError, ValueError:
        pass

    def verify(items):
        for candidate in dict.fromkeys(ip for ip in items if isinstance(ip, str) and ip):
            stop.check()
            if not on_link(candidate, networks):
                continue
            try:
                paired_local_session(candidate, serial)
                try:
                    atomic(CACHE, {"ip": candidate, "serial": serial, "verified_at": time.time()})
                except OSError:
                    pass
                return candidate
            except OSError, RuntimeError, ValueError, KeyError:
                pass
        return None

    found = verify(candidates)
    if found:
        return found
    # Bonjour is only a source of candidates; certificate + UDID remain mandatory.
    try:
        from pymobiledevice3.bonjour import browse_mobdev2

        services = asyncio.run(browse_mobdev2(timeout=2))
        return verify([address.ip for service in services for address in service.addresses])
    except OSError, RuntimeError, ValueError:
        return None


def run_automatic(job, stop, accounts=None):
    from iphone_detector import remote_tunnel
    from iphone_detector.background import run_background
    from iphone_detector.bridge_service import installed
    from iphone_detector.config import load_config
    from iphone_detector.wifi import WifiBridge

    serial = (load_config() or {}).get("device", {}).get("serial", "")
    if find_usb(serial):
        stop.check()
        remote_tunnel.atomic(job / "route.json", {"transport": "usb"})
        return dict(run_background(job, stop, "USB", accounts=accounts), transport="usb")
    local = find_local(serial, remote_tunnel.settings(), stop)
    if local and remote_tunnel.enabled():
        try:
            return run_local_tunnel(job, stop, local, accounts=accounts)
        except LocalRefreshUnavailable:
            stop.check()
            LOG.info("Local tunnel preflight failed; trying the existing Wi-Fi route.")
    if local and installed():
        bridge = WifiBridge(job, serial, stop, allow_elevation=False, connect_timeout=15)
        try:
            bridge.__enter__()
        except Exception:
            stop.check()
            if not bridge.cleanup_verified:
                raise RuntimeError(
                    "Local connection cleanup could not be verified. Remote fallback cancelled."
                )
            LOG.info("Local Wi-Fi unavailable after discovery; trying the existing remote tunnel.")
        else:
            try:
                remote_tunnel.atomic(
                    job / "route.json", {"transport": "local-wifi", "paired_device_verified": True}
                )
                LOG.info(
                    "Selected fast local Wi-Fi after paired TLS and device/service verification."
                )
                result = run_background(job, stop, "Network", accounts=accounts)
                return dict(result, transport="local-wifi")
            finally:
                bridge.__exit__(None, None, None)
    stop.check()
    remote_tunnel.atomic(
        job / "route.json", {"transport": "remote", "local_device_verified": False}
    )
    LOG.info("Selected persistent remote tunnel; no authenticated local Wi-Fi path available.")
    return dict(remote_tunnel.submit(job, stop, accounts), transport="remote")


def run_local_tunnel(job, stop, address, accounts=None, refresh=False):
    """Run once over a direct LAN tunnel; only preflight failure permits fallback."""
    from iphone_detector import remote_tunnel
    from iphone_detector.background import make_job
    from iphone_detector.config import load_config
    from pymobiledevice3.exceptions import PyMobileDevice3Exception

    config = remote_tunnel.settings()
    request = make_job(load_config() or {}, "Remote", accounts=accounts)
    if canonical_serial(config.get("serial", "")) != canonical_serial(request["serial"]):
        raise RuntimeError("Local tunnel configuration refers to a different phone.")
    if not on_link(address, local_networks()):
        raise LocalRefreshUnavailable("The local phone is no longer on this network.")

    async def execute():
        session = remote_tunnel.open_session(config, address)
        try:
            client = await session.__aenter__()
        except (OSError, TimeoutError, PyMobileDevice3Exception) as error:
            raise LocalRefreshUnavailable("Local tunnel preflight is unavailable.") from error
        try:
            stop.check()
            if refresh:
                (job / "refresh-request").touch()
            remote_tunnel.atomic(
                job / "route.json", {"transport": "local-wifi", "protocol": "remote-pairing"}
            )
            remote_tunnel.atomic(
                job / "remote-request.json",
                {"accounts": [a["account"] for a in request["accounts"]], "execution": "local"},
            )
            operation = asyncio.create_task(remote_tunnel.execute_job(job, client))
            try:
                while not operation.done():
                    (job / "remote-heartbeat").touch()
                    if stop.cancelled.is_set():
                        (job / "stop").touch()
                    await asyncio.sleep(0.2)
                await operation
            finally:
                if not operation.done():
                    operation.cancel()
                    await asyncio.gather(operation, return_exceptions=True)
        finally:
            await session.__aexit__(None, None, None)

    asyncio.run(execute())
    result = json.loads((job / "remote-result.json").read_text(encoding="utf-8"))
    remote_tunnel.completed_result(result, len(request["accounts"]))
    return dict(result, transport="local-wifi")
