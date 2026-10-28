"""Read-only Tailscale/Apple compatibility check. Does not install apps or switch services."""

import argparse
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import socket
import ssl
import subprocess
import tempfile

from iphone_detector.wifi import canonical_serial, exact, lockdown


def select_peer(status, requested=None):
    if not isinstance(status, dict) or status.get("BackendState") != "Running":
        raise RuntimeError("Sign this PC into Tailscale first.")
    all_peers = status.get("Peer") or {}
    if not isinstance(all_peers, dict):
        raise RuntimeError("Tailscale returned an invalid peer list. Try again later.")
    peers = [
        p
        for p in all_peers.values()
        if isinstance(p, dict)
        and isinstance(p.get("OS"), str)
        and p["OS"].lower() == "ios"
        and isinstance(p.get("TailscaleIPs"), list)
        and all(isinstance(ip, str) for ip in p["TailscaleIPs"])
    ]
    if requested:
        peers = [p for p in peers if requested in p.get("TailscaleIPs", [])]
    if len(peers) != 1:
        raise RuntimeError("Select exactly one iPhone Tailscale address using --ip.")
    peer = peers[0]
    if peer.get("Online") is False:
        raise RuntimeError("The selected iPhone is offline in Tailscale.")
    addresses = []
    for ip in peer["TailscaleIPs"]:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10"):
            addresses.append(ip)
    if requested:
        addresses = [ip for ip in addresses if ip == requested]
    if len(addresses) != 1:
        raise RuntimeError("An IPv4 Tailscale address for this iPhone is required.")
    return addresses[0]


def read_pair(serial):
    folder = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Apple/Lockdown"
    matches = [
        p for p in folder.glob("*.plist") if canonical_serial(p.stem) == canonical_serial(serial)
    ]
    if len(matches) != 1:
        raise RuntimeError("The saved iPhone needs an existing Apple USB pairing record.")
    return plistlib.loads(matches[0].read_bytes())


def pairing_identifier(serial):
    """Use the record's original identifier for APIs with exact cache-key matching."""
    from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier

    matches = {
        identifier
        for identifier, _, _ in iter_remote_pair_records_by_identifier()
        if canonical_serial(identifier) == canonical_serial(serial)
    }
    if len(matches) > 1:
        raise ValueError(
            "Multiple remote pairing records match this phone. Review pairing locally before reconnecting."
        )
    return next(iter(matches), serial)


def tls_context(pair):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Pin the existing paired device certificate below.
    context.set_ciphers("ALL:@SECLEVEL=0")
    with tempfile.TemporaryDirectory(prefix="ipa-remote-pair-") as directory:
        cert, key = Path(directory) / "host.pem", Path(directory) / "host-key.pem"
        cert.write_bytes(pair["HostCertificate"])
        key.write_bytes(pair["HostPrivateKey"])
        context.load_cert_chain(cert, key)
    return context


def pinned(context, stream, pair):
    expected = ssl.PEM_cert_to_DER_cert(pair["DeviceCertificate"].decode())
    secure = context.wrap_socket(stream)
    if secure.getpeercert(binary_form=True) != expected:
        secure.close()
        raise RuntimeError("Remote device certificate does not match the paired iPhone.")
    return secure


def authenticate_direct(ip, serial, pair, stage=lambda _: None):
    stage("lockdown_connect")
    with socket.create_connection((ip, 62078), timeout=8) as raw:
        stage("paired_session")
        session = lockdown(
            raw,
            {"Request": "StartSession", "HostID": pair["HostID"], "SystemBUID": pair["SystemBUID"]},
        )
        if not session.get("EnableSessionSSL"):
            raise RuntimeError("Remote pairing session did not enable authenticated TLS.")
        context = tls_context(pair)
        stream = pinned(context, raw, pair)
        try:
            stage("verify_device")
            udid = lockdown(stream, {"Request": "GetValue", "Key": "UniqueDeviceID"})["Value"]
            if canonical_serial(udid) != canonical_serial(serial):
                raise RuntimeError("The remote device does not match the configured iPhone.")
            stage("installation_service")
            service = lockdown(
                stream,
                {"Request": "StartService", "Service": "com.apple.mobile.installation_proxy"},
            )
            port = service["Port"]
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise RuntimeError("Device returned an invalid installation-service port.")
            if service.get("EnableServiceSSL") is not True:
                raise RuntimeError("Installation service did not enable authenticated TLS.")
            with socket.create_connection((ip, port), timeout=8) as service_raw:
                service_stream = pinned(context, service_raw, pair)
                try:
                    stage("read_only_lookup")
                    payload = plistlib.dumps(
                        {
                            "Command": "Lookup",
                            "ClientOptions": {
                                "ApplicationType": "User",
                                "ReturnAttributes": ["CFBundleIdentifier"],
                            },
                        }
                    )
                    import struct

                    service_stream.sendall(struct.pack("!I", len(payload)) + payload)
                    reply = plistlib.loads(
                        exact(service_stream, struct.unpack("!I", exact(service_stream, 4))[0])
                    )
                    if reply.get("Status") != "Complete" or reply.get("Error"):
                        raise RuntimeError(
                            "Installation service did not complete its read-only lookup."
                        )
                finally:
                    if service_stream is not service_raw:
                        service_stream.close()
            lockdown(stream, {"Request": "StopSession", "SessionID": session["SessionID"]})
            return {"paired_device_verified": True, "installation_service": True}
        finally:
            if stream is not raw:
                stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ip", help="Select the iPhone address already present in the Tailscale peer list"
    )
    parser.add_argument(
        "--serial", help="Configured paired iPhone serial; defaults to manager settings"
    )
    parser.add_argument(
        "--away",
        action="store_true",
        help="Record that you confirmed the iPhone is off the home network",
    )
    args = parser.parse_args()
    from iphone_detector.config import load_config
    from iphone_detector.paths import DATA_ROOT

    report = {
        "ok": False,
        "read_only": True,
        "away_confirmed_by_user": args.away,
        "stage": "tailscale",
    }

    def stage(value):
        report["stage"] = value
        print("Checking " + value + "...", flush=True)

    try:
        binary = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale/tailscale.exe"
        )
        status = subprocess.run(
            [str(binary), "status", "--json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        ip = select_peer(json.loads(status.stdout), args.ip)
        serial = args.serial or (load_config() or {}).get("device", {}).get("serial")
        if not serial:
            raise RuntimeError("Pair your iPhone using the manager first.")
        report["ip"] = ip
        stage("read_existing_pair_record")
        report.update(authenticate_direct(ip, serial, read_pair(serial), stage))
        report["ok"] = True
        report["stage"] = "complete"
    except Exception as exc:
        # Never serialize pairing records, certificates, keys or session identifiers.
        report["error_type"] = type(exc).__name__
        report["error"] = (
            str(exc)
            if isinstance(exc, (RuntimeError, OSError))
            else "Compatibility check failed; inspect this stage."
        )
    folder = (
        DATA_ROOT
        / "automation-runs"
        / ("tailscale-check-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
