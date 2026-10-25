"""Find authenticated phone listeners when multicast discovery is unavailable."""

import asyncio
import ipaddress
import secrets
import socket
import struct
import threading


def query_local(address, identifier):
    """Query one previously authenticated LAN phone; trust only its pairing tag."""
    from pymobiledevice3.bonjour import (
        Address,
        ServiceInstance,
        encode_name,
        parse_mdns_message,
    )
    from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier
    from pymobiledevice3.remote.tunnel_service import _match_remote_pair_record

    ip = ipaddress.IPv4Address(address)
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        raise ValueError("Listener discovery requires an authenticated local phone.")
    keys = {
        name: record["peer_alt_irk"]
        for name, _, record in iter_remote_pair_records_by_identifier()
        if name == identifier and "peer_alt_irk" in record
    }
    if not keys:
        return []
    service_type = "_remotepairing._tcp.local."
    transaction = secrets.randbelow(65535) + 1
    query = (
        struct.pack("!6H", transaction, 0, 1, 0, 0, 0)
        + encode_name(service_type)
        + struct.pack("!2H", 12, 1)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.settimeout(3)
        stream.sendto(query, (str(ip), 5353))
        packet, sender = stream.recvfrom(65535)
    if sender != (str(ip), 5353) or len(packet) < 12:
        return []
    txid, flags, questions, answers, authorities, additional = struct.unpack("!6H", packet[:12])
    if txid != transaction or not flags & 0x8000 or flags & 0xF or questions > 16:
        return []
    if answers + authorities + additional > 256:
        return []
    records = parse_mdns_message(packet)
    result = []
    for srv in records:
        if (
            srv["type"] != 33
            or not srv["name"].endswith("." + service_type)
            or not 1 <= srv.get("port", 0) <= 65535
            or srv.get("ttl", 0) <= 0
        ):
            continue
        properties = next(
            (r["txt"] for r in records if r["type"] == 16 and r["name"] == srv["name"]), {}
        )
        answer = ServiceInstance(
            srv["name"], srv["target"], srv["port"], [Address(str(ip), "")], properties
        )
        if _match_remote_pair_record(answer, keys) == identifier:
            result.append(answer)
    return result


async def discover(serial, config):
    """Return authenticated services, trying the paired LAN candidate as fallback."""
    from pymobiledevice3.remote.tunnel_service import (
        create_core_device_tunnel_service_using_remotepairing,
        get_remote_pairing_tunnel_services,
    )
    from iphone_detector.background import BackgroundStop
    from iphone_detector.connection_router import find_local
    from iphone_detector.remote_check import pairing_identifier

    identifier = pairing_identifier(serial)
    try:
        services = await asyncio.wait_for(
            get_remote_pairing_tunnel_services(bonjour_timeout=2, udid=identifier), 10
        )
    except OSError, asyncio.TimeoutError:
        services = []
    if services:
        return services
    address = await asyncio.to_thread(find_local, serial, config, BackgroundStop(threading.Event()))
    if not address:
        return []
    try:
        answers = await asyncio.to_thread(query_local, address, identifier)
    except OSError, ValueError, KeyError, IndexError, UnicodeError, struct.error:
        return []
    for answer in answers[:3]:
        try:
            service = await asyncio.wait_for(
                create_core_device_tunnel_service_using_remotepairing(
                    identifier, address, answer.port, autopair=False
                ),
                12,
            )
            return [service]
        except OSError, asyncio.TimeoutError:
            continue
    return []
