"""Normal-user client and one-time installation for the fixed Wi-Fi broker."""

import ctypes
import hashlib
import os
from pathlib import Path
import subprocess
import time

from iphone_detector.paths import ASSET_ROOT

NAME = "iPhoneIPAManagerBridge"
PIPE = r"\\.\pipe\iPhoneIPAManagerBridge"


def installed():
    import win32service
    import pywintypes

    try:
        manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    except pywintypes.error as exc:
        raise OSError("Windows could not inspect the Wi-Fi bridge service.") from exc
    try:
        try:
            handle = win32service.OpenService(manager, NAME, win32service.SERVICE_QUERY_STATUS)
        except pywintypes.error as exc:
            if exc.winerror == 1060:
                return False
            raise OSError("Windows could not inspect the Wi-Fi bridge service.") from exc
        win32service.CloseServiceHandle(handle)
        return True
    finally:
        win32service.CloseServiceHandle(manager)


class ServiceLease:
    def __init__(self):
        import win32file
        import win32pipe
        import pywintypes

        # Never silently elevate from a remote command.
        try:
            win32pipe.WaitNamedPipe(PIPE, 5000)
            self.handle = win32file.CreateFile(PIPE, 0xC0000000, 0, None, 3, 0, None)
        except pywintypes.error as exc:
            raise OSError("Wi-Fi bridge service is unavailable. Check the local service.") from exc
        self.buffer = b""
        try:
            win32file.WriteFile(self.handle, b"START\n")
            if self.read(40) != "RUNNING":
                raise RuntimeError("Wi-Fi service could not start the bridge.")
        except pywintypes.error as exc:
            self.handle.Close()
            self.handle = None
            raise OSError("Could not start the Wi-Fi bridge service.") from exc
        except BaseException:
            self.handle.Close()
            self.handle = None
            raise

    def read(self, timeout):
        import win32file
        import win32pipe
        import pywintypes

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                return line.decode("ascii").strip()
            try:
                _, count, _ = win32pipe.PeekNamedPipe(self.handle, 0)
                if count:
                    self.buffer += win32file.ReadFile(self.handle, min(count, 4096))[1]
                else:
                    time.sleep(0.1)
            except pywintypes.error as exc:
                raise OSError("Wi-Fi bridge service connection was interrupted.") from exc
        raise RuntimeError("Wi-Fi service response timed out.")

    def close(self):
        import win32file
        import pywintypes

        if self.handle is None:
            return
        try:
            win32file.WriteFile(self.handle, b"STOP\n")
            if self.read(40) != "STOPPED":
                raise RuntimeError("Wi-Fi service could not verify Apple service restoration.")
        except pywintypes.error as exc:
            raise OSError("Wi-Fi bridge service cleanup could not be verified.") from exc
        finally:
            self.handle.Close()
            self.handle = None


def current_sid():
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        return win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        )
    finally:
        token.Close()


def install(owner_sid):
    """Called once under UAC. Service binaries/data never live in a user-writable folder."""
    import re
    import win32service
    from iphone_detector.wifi import BRIDGE_HASH

    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Installing the Wi-Fi service requires administrator approval once.")
    if not re.fullmatch(r"S-1-5-21-\d+-\d+-\d+-\d+", owner_sid or ""):
        raise ValueError("A local Windows user SID is required.")
    root = Path(os.environ["ProgramFiles"]) / "iPhone IPA Manager Bridge"
    root.mkdir(exist_ok=True)
    # Fixed ACL: administrators/System can modify; ordinary users can only read/execute.
    subprocess.run(
        [
            "icacls.exe",
            str(root),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-32-545:(OI)(CI)RX",
        ],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    handle = None
    try:
        if installed():
            handle = win32service.OpenService(manager, NAME, win32service.SERVICE_ALL_ACCESS)
            if win32service.QueryServiceStatus(handle)[1] != win32service.SERVICE_STOPPED:
                raise RuntimeError(
                    "Service already installed/running. Stop it before reinstalling."
                )
        binary = (ASSET_ROOT / "tools/netmuxd/netmuxd.exe").read_bytes()
        if hashlib.sha256(binary).hexdigest() != BRIDGE_HASH:
            raise RuntimeError("Bundled bridge integrity check failed.")
        (root / "netmuxd.exe").write_bytes(binary)
        (root / "BridgeService.exe").write_bytes(
            (ASSET_ROOT / "tools/wifi-service/BridgeService.exe").read_bytes()
        )
        (root / "owner.sid").write_text(owner_sid, encoding="ascii")
        if handle is None:
            handle = win32service.CreateService(
                manager,
                NAME,
                "iPhone IPA Manager Wi-Fi Bridge",
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                '"' + str(root / "BridgeService.exe") + '"',
                None,
                0,
                None,
                None,
                None,
            )
        win32service.ChangeServiceConfig2(
            handle,
            win32service.SERVICE_CONFIG_DESCRIPTION,
            "Fixed-purpose local Wi-Fi bridge for iPhone IPA Manager. Restores Apple service after each run.",
        )
        win32service.ChangeServiceConfig2(
            handle,
            win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
            {
                "ResetPeriod": 86400,
                "RebootMsg": "",
                "Command": "",
                "Actions": [(1, 3000), (1, 10000), (1, 30000)],
            },
        )
        win32service.StartService(handle, None)
    finally:
        if handle:
            win32service.CloseServiceHandle(handle)
        win32service.CloseServiceHandle(manager)
