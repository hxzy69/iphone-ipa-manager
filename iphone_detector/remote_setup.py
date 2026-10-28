"""One-time trusted RemotePairing setup for the optional Tailscale transport."""

import asyncio
from contextlib import AsyncExitStack
import ipaddress
import json
import os
from pathlib import Path
import subprocess

from iphone_detector.remote_check import read_pair, select_peer, pairing_identifier
from iphone_detector.wifi import canonical_serial


def tailscale_address(requested=None):
    if requested:
        address = ipaddress.ip_address(requested.strip())
        if address not in ipaddress.ip_network("100.64.0.0/10"):
            raise ValueError("Enter the iPhone IPv4 address shown in Tailscale (100.x.x.x).")
        requested = str(address)
    binary = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"
    if not binary.is_file():
        raise ValueError(
            "Install Tailscale on this PC and sign in to the same network as your iPhone."
        )
    status = json.loads(
        subprocess.check_output(
            [str(binary), "status", "--json"], timeout=15, creationflags=subprocess.CREATE_NO_WINDOW
        )
    )
    return select_peer(status, requested)


async def provision(serial, phone_ip, lan_ip=None):
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.lockdown import create_using_tcp, create_using_usbmux
    from pymobiledevice3.remote.tunnel_service import (
        RemotePairingLockdownService,
        create_core_device_tunnel_service_using_remotepairing,
    )

    pair = read_pair(serial)
    if lan_ip:
        address = ipaddress.ip_address(lan_ip.strip())
        if not address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("Use the iPhone local network address for LAN pairing.")
        client = await create_using_tcp(
            str(address), identifier=serial, pair_record=pair, autopair=False, keep_alive=True
        )
    else:
        from iphone_detector.wifi import devices

        matches = [
            device
            for device in devices()
            if device.get("Properties", {}).get("ConnectionType") == "USB"
            and canonical_serial(device["Properties"].get("SerialNumber", ""))
            == canonical_serial(serial)
        ]
        if len(matches) != 1:
            raise ValueError(
                "Connect the selected iPhone by USB, unlock it, and approve Trust first."
            )
        client = await create_using_usbmux(
            serial=matches[0]["Properties"]["SerialNumber"],
            identifier=serial,
            pair_record=pair,
            autopair=False,
            connection_type="USB",
        )
    try:
        if canonical_serial(client.udid or "") != canonical_serial(serial):
            raise ValueError("The connected iPhone does not match the saved device.")
        remote_identifier = client.udid
        service = await RemotePairingLockdownService.create(client)
        try:
            try:
                await service.connect(autopair=True)
            except RemotePairingCompletedError:
                await service.close()
                service = await RemotePairingLockdownService.create(client)
                await service.connect(autopair=False)
            if not service.encryption_key:
                raise ValueError("The iPhone did not authenticate RemotePairing.")
        finally:
            await service.close()
    finally:
        await client.close()
    remote_identifier = pairing_identifier(remote_identifier)
    from iphone_detector.remote_discovery import discover

    async with AsyncExitStack() as services_stack:
        services = await discover(remote_identifier, {"ip": phone_ip})
        for service in services:
            services_stack.push_async_callback(service.close)
        ports = sorted({service.port for service in services})
    for port in ports:
        try:
            remote = await asyncio.wait_for(
                create_core_device_tunnel_service_using_remotepairing(
                    remote_identifier, phone_ip, port, autopair=False
                ),
                timeout=12,
            )
            try:
                if remote.encryption_key:
                    return {
                        "enabled": True,
                        "serial": remote_identifier,
                        "ip": phone_ip,
                        "port": port,
                    }
            finally:
                await remote.close()
        except OSError, asyncio.TimeoutError:
            continue
    raise ValueError(
        "No authenticated VPN listener was found. Keep both devices on home Wi-Fi with Tailscale connected and the phone unlocked, then retry."
    )


def configure(phone_ip=None, lan_ip=None):
    from iphone_detector.config import load_config
    from iphone_detector.run_lock import RunLock
    from iphone_detector import remote_tunnel

    serial = (load_config() or {}).get("device", {}).get("serial")
    if not serial:
        raise ValueError("Select your iPhone by USB in the main app first.")
    if remote_tunnel.state()["state"] != "offline":
        raise ValueError(
            "A remote tunnel is already active. Keep it open; setup is not needed again."
        )
    guard = RunLock()
    if not guard.acquire():
        raise ValueError("Wait for the current installation or sign-in to finish.")
    try:
        address = tailscale_address(phone_ip)
        settings = asyncio.run(asyncio.wait_for(provision(serial, address, lan_ip), timeout=75))
        remote_tunnel.atomic(remote_tunnel.SETTINGS, settings)
    finally:
        guard.close()
    remote_tunnel.ensure_started()
    return settings
