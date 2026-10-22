"""Temporary network usbmux bridge for the native Windows backend.

The fixed-purpose service is installed separately. Application workers keep
their normal permissions. Pairing credentials are never written to logs.
"""

import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import plistlib
import socket
import ssl
import struct
import subprocess
import tempfile
import time

from iphone_detector.paths import ASSET_ROOT, FROZEN

LOG = logging.getLogger("iphone-detector.wifi")
SERVICE = "Apple Mobile Device Service"
BRIDGE_HASH = "8a926491603237a911150c2e05850cb3a6cb52f6b869c7049a0f91e5cf61bb80"
MAX_PACKET = 16 * 1024 * 1024


def cache_executable(binary, root):
    """Keep the companion available after a one-file worker's extraction exits."""
    contents = Path(binary).read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    destination = Path(root) / digest / "iloader-wifi.exe"
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(contents)
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def canonical_serial(value):
    return "".join(c for c in value.upper() if c.isalnum())


def exact(stream, length):
    if not 0 <= length <= MAX_PACKET:
        raise ValueError("Invalid device packet size")
    data = b""
    while len(data) < length:
        chunk = stream.recv(length - len(data))
        if not chunk:
            raise ConnectionError("Device connection closed")
        data += chunk
    return data


def mux_request(stream, request):
    payload = plistlib.dumps(
        dict(request, ClientVersionString="iPhoneIPAManager", kLibUSBMuxVersion=3)
    )
    stream.sendall(struct.pack("<IIII", len(payload) + 16, 1, 8, 1) + payload)
    size, version, message, _ = struct.unpack("<IIII", exact(stream, 16))
    if version != 1 or message != 8:
        raise ValueError("Unexpected usbmux response")
    return plistlib.loads(exact(stream, size - 16))


def devices(port=27015):
    with socket.create_connection(("127.0.0.1", port), timeout=4) as stream:
        return mux_request(stream, {"MessageType": "ListDevices"}).get("DeviceList", [])


def select_network_device(items, serial):
    matches = [
        d
        for d in items
        if d.get("Properties", {}).get("ConnectionType") == "Network"
        and canonical_serial(d["Properties"].get("SerialNumber", "")) == canonical_serial(serial)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "The saved iPhone is not uniquely available over Wi-Fi. Keep it unlocked on the same network."
        )
    if len(items) != 1:
        raise RuntimeError(
            "More than one device is visible. Disconnect other devices before automatic Wi-Fi installation."
        )
    return matches[0]


def lockdown(stream, request):
    payload = plistlib.dumps(dict(request, Label="iPhoneIPAManager"))
    stream.sendall(struct.pack("!I", len(payload)) + payload)
    result = plistlib.loads(exact(stream, struct.unpack("!I", exact(stream, 4))[0]))
    if result.get("Error"):
        raise RuntimeError("iPhone rejected %s: %s" % (request["Request"], result["Error"]))
    return result


def authenticate(serial, port=27015):
    """Read-only session test, including access to Installation Proxy."""
    device = select_network_device(devices(port), serial)
    udid = device["Properties"]["SerialNumber"]
    with socket.create_connection(("127.0.0.1", port), timeout=8) as stream:
        response = mux_request(stream, {"MessageType": "ReadPairRecord", "PairRecordID": udid})
    if "PairRecordData" not in response:
        raise RuntimeError("No Apple pairing record. Connect by USB and trust this computer first.")
    pair = plistlib.loads(response["PairRecordData"])
    with socket.create_connection(("127.0.0.1", port), timeout=8) as raw:
        response = mux_request(
            raw,
            {
                "MessageType": "Connect",
                "DeviceID": device["DeviceID"],
                "PortNumber": socket.htons(62078),
            },
        )
        if response.get("Number") != 0:
            raise RuntimeError("Cannot open the wireless device connection")
        session = lockdown(
            raw,
            {"Request": "StartSession", "HostID": pair["HostID"], "SystemBUID": pair["SystemBUID"]},
        )
        stream = raw
        context = None
        try:
            if session.get("EnableSessionSSL"):
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                # Apple authenticates with the existing paired host certificate.
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                context.set_ciphers("ALL:@SECLEVEL=0")
                with tempfile.TemporaryDirectory(prefix="ipa-pair-") as temporary:
                    cert, key = Path(temporary) / "host.pem", Path(temporary) / "host-key.pem"
                    cert.write_bytes(pair["HostCertificate"])
                    key.write_bytes(pair["HostPrivateKey"])
                    context.load_cert_chain(cert, key)
                stream = context.wrap_socket(raw)
                # Verify the paired phone's certificate rather than a public CA.
                expected = ssl.PEM_cert_to_DER_cert(pair["DeviceCertificate"].decode())
                if stream.getpeercert(binary_form=True) != expected:
                    raise RuntimeError(
                        "Wireless phone certificate does not match its pairing record"
                    )
            name = lockdown(stream, {"Request": "GetValue", "Key": "DeviceName"})["Value"]
            service = lockdown(
                stream,
                {"Request": "StartService", "Service": "com.apple.mobile.installation_proxy"},
            )
            # Verify the installation service actually answers, without installing anything.
            with socket.create_connection(("127.0.0.1", port), timeout=8) as service_raw:
                connected = mux_request(
                    service_raw,
                    {
                        "MessageType": "Connect",
                        "DeviceID": device["DeviceID"],
                        "PortNumber": socket.htons(service["Port"]),
                    },
                )
                if connected.get("Number") != 0:
                    raise RuntimeError("Wireless installation service is unreachable")
                if service.get("EnableServiceSSL") and context is None:
                    raise RuntimeError("Installation service requires an authenticated TLS session")
                service_stream = (
                    context.wrap_socket(service_raw)
                    if service.get("EnableServiceSSL")
                    else service_raw
                )
                try:
                    payload = plistlib.dumps(
                        {
                            "Command": "Lookup",
                            "ClientOptions": {
                                "ApplicationType": "User",
                                "ReturnAttributes": ["CFBundleIdentifier"],
                            },
                        }
                    )
                    service_stream.sendall(struct.pack("!I", len(payload)) + payload)
                    reply = plistlib.loads(
                        exact(service_stream, struct.unpack("!I", exact(service_stream, 4))[0])
                    )
                    if reply.get("Status") != "Complete" or reply.get("Error"):
                        raise RuntimeError(
                            "Wireless installation service did not complete its read-only check"
                        )
                finally:
                    if service_stream is not service_raw:
                        service_stream.close()
            lockdown(stream, {"Request": "StopSession", "SessionID": session["SessionID"]})
            return {
                "name": name,
                "serial": udid,
                "connection": "Network",
                "installation_service": True,
            }
        finally:
            if stream is not raw:
                stream.close()


def write_status(job, **values):
    temporary = job / "bridge-status.tmp"
    temporary.write_text(json.dumps(values), encoding="utf-8")
    temporary.replace(job / "bridge-status.json")


def service_state():
    result = subprocess.run(
        ["sc.exe", "query", SERVICE],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Apple Mobile Device Service is not installed")
    # sc.exe's numeric state remains stable across Windows UI languages.
    import re

    match = re.search(
        r":\s*([1-7])\s+(?:STOPPED|START_PENDING|STOP_PENDING|RUNNING|CONTINUE_PENDING|PAUSE_PENDING|PAUSED)",
        result.stdout,
    )
    if not match:
        raise RuntimeError("Could not determine Apple service state")
    return int(match.group(1))


def change_service(action, state):
    result = subprocess.run(
        ["sc.exe", action, SERVICE],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if service_state() == state:
            return
        time.sleep(0.25)
    raise RuntimeError(
        "Could not %s Apple Mobile Device Service (code %s)" % (action, result.returncode)
    )


def process_handle(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        raise RuntimeError("The Wi-Fi controller has already exited")
    return kernel, handle


def supervise(job, parent_pid):
    """Elevated helper: temporary service switch, with restoration in finally."""
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Wi-Fi bridge requires Windows administrator approval")
    kernel, parent = process_handle(parent_pid)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    mutex = kernel.CreateMutexW(None, True, "Global\\iPhoneIPAManagerWifiBridge")
    duplicate = ctypes.get_last_error() == 183
    bridge = None
    restore = False
    failure = None
    try:
        if not mutex or duplicate:
            raise RuntimeError("Another Wi-Fi bridge is already active")
        binary = ASSET_ROOT / "tools/netmuxd/netmuxd.exe"
        if hashlib.sha256(binary.read_bytes()).hexdigest() != BRIDGE_HASH:
            raise RuntimeError("Wi-Fi bridge integrity check failed")
        state = service_state()
        if state not in (1, 4):
            raise RuntimeError("Apple service is changing state. Wait a few seconds and retry.")
        if state == 4:
            restore = True  # Also restore if stopping fails partway through.
            change_service("stop", 1)
        if kernel.WaitForSingleObject(parent, 0) != 258 or (job / "bridge-stop").exists():
            return 130
        with (job / "bridge.log").open("w", encoding="utf-8") as output:
            env = os.environ.copy()
            env["RUST_LOG"] = "info"
            bridge = subprocess.Popen(
                [str(binary), "--disable-usb", "--host", "127.0.0.1", "--port", "27015"],
                stdout=output,
                stderr=output,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            write_status(job, state="running")
            while (
                kernel.WaitForSingleObject(parent, 500) == 258
                and not (job / "bridge-stop").exists()
            ):
                if bridge.poll() is not None:
                    raise RuntimeError("Wi-Fi bridge exited. See bridge.log.")
        return 0
    except Exception as exc:
        failure = str(exc)
        write_status(job, state="error", error=str(exc))
        return 1
    finally:
        try:
            try:
                if bridge is not None and bridge.poll() is None:
                    bridge.terminate()
                    bridge.wait(timeout=8)
            finally:
                if restore:
                    change_service("start", 4)
            write_status(job, state="stopped", apple_service_restored=True, error=failure)
        except Exception as exc:
            write_status(job, state="restore-error", error=str(exc))
        if mutex:
            kernel.CloseHandle(mutex)
        kernel.CloseHandle(parent)


def elevate(job):
    arguments = [] if FROZEN else [str(ASSET_ROOT / "ipa_manager.py")]
    arguments += ["--worker", "wifi-admin", "--job-dir", str(job), "--parent-pid", str(os.getpid())]
    return elevate_arguments(arguments)


def elevate_arguments(arguments):
    raise RuntimeError(
        "Administrator prompts are disabled. This operation requires a service installed separately by an administrator."
    )


class WifiBridge:
    def __init__(self, job, serial, stop, allow_elevation=False, connect_timeout=65):
        self.job, self.serial, self.stop = Path(job), serial, stop
        self.allow_elevation = allow_elevation
        self.handle = None
        self.lease = None
        self.connect_timeout = connect_timeout
        self.cleanup_verified = False

    def __enter__(self):
        try:
            from iphone_detector.bridge_service import installed, ServiceLease

            if installed():
                LOG.info("Using installed Wi-Fi service; no administrator prompt needed.")
                self.lease = ServiceLease()
                write_status(self.job, state="running", service=True)
            else:
                raise RuntimeError(
                    "The Wi-Fi service is unavailable. Administrator prompts are disabled; service setup must be performed separately by an administrator."
                )
            deadline = time.monotonic() + self.connect_timeout
            last_error = "Phone not discovered yet"
            while time.monotonic() < deadline:
                self.stop.check()
                status_file = self.job / "bridge-status.json"
                status = json.loads(status_file.read_text()) if status_file.exists() else {}
                if status.get("state") in ("error", "restore-error", "stopped"):
                    raise RuntimeError(
                        status.get("error", "Wi-Fi helper stopped before connecting")
                    )
                if status.get("state") == "running":
                    try:
                        self.device = authenticate(self.serial)
                        LOG.info(
                            "Wi-Fi authenticated: %s. Installation service passed its read-only test.",
                            self.device["name"],
                        )
                        return self
                    except (OSError, RuntimeError, ValueError, KeyError) as exc:
                        last_error = str(exc)
                self.stop.sleep(1)
            raise RuntimeError("Wi-Fi connection timed out: " + last_error)
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.lease:
            try:
                self.lease.close()
                write_status(self.job, state="stopped", apple_service_restored=True, service=True)
                LOG.info("Wi-Fi service released bridge. Apple connection service restored.")
            finally:
                self.lease = None
        if self.handle:
            (self.job / "bridge-stop").touch()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            finished = kernel.WaitForSingleObject(self.handle, 40000) == 0
            kernel.CloseHandle(self.handle)
            self.handle = None
            if not finished:
                raise RuntimeError(
                    "Wi-Fi helper is still restoring Apple service. See bridge-status.json."
                )
            status = json.loads((self.job / "bridge-status.json").read_text())
            if status.get("state") != "stopped" or not status.get("apple_service_restored"):
                raise RuntimeError(
                    status.get("error", "Apple service restoration could not be verified")
                )
            LOG.info("Wi-Fi bridge stopped. Apple connection service restored.")
        self.cleanup_verified = True
