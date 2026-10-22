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
                network = ipaddress.ip_network((address.ip, address.network_prefix), strict=False)
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
    peer = next(p for p in status["Peer"].values() if selected in p.get("TailscaleIPs", []))
    endpoint = peer.get("CurAddr") or ""
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
        if canonical_serial(cached.get("serial", "")) == canonical_serial(serial):
            candidates.append(cached.get("ip"))
    except OSError, ValueError:
        pass

    def verify(items):
        for candidate in dict.fromkeys(ip for ip in items if ip):
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
    local = find_local(serial, remote_tunnel.settings(), stop)
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
