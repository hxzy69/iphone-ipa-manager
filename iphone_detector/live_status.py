"""Read-only, owner-restricted Windows IPC for background status.

Status files remain the execution authority. This channel lets desktop clients
notice a different/stale filesystem view without copying settings or replaying work.
"""

import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

LOG = logging.getLogger("iphone-detector.live-status")
STATES = {"ready", "installing", "waiting", "connecting", "offline"}
LIMIT = 4096


def address(root):
    from iphone_detector.bridge_service import current_sid

    identity = current_sid() + "|" + str(Path(root).resolve()).casefold()
    return r"\\.\pipe\iPhoneIPAStatus-" + hashlib.sha256(identity.encode()).hexdigest()


def connect(name):
    import _winapi
    from multiprocessing.connection import PipeConnection

    # Unlike PipeClient's retry loop, a busy/missing server fails immediately.
    handle = _winapi.CreateFile(
        name,
        _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
        0,
        _winapi.NULL,
        _winapi.OPEN_EXISTING,
        _winapi.FILE_FLAG_OVERLAPPED,
        _winapi.NULL,
    )
    try:
        _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
        return PipeConnection(handle)
    except Exception:
        _winapi.CloseHandle(handle)
        raise


def server_pid(connection):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    result = wintypes.ULONG()
    if not kernel.GetNamedPipeServerProcessId(connection.fileno(), ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return result.value


def trusted_server(pid, root):
    import psutil
    from iphone_detector.paths import process_uses_profile

    process = psutil.Process(pid)
    args = process.cmdline()
    return (
        Path(process.exe()).resolve() == Path(sys.executable).resolve()
        and "--remote-daemon" in args
        and process_uses_profile(args[1:], root)
    )


def query(root):
    """Return a bounded live display snapshot, or None; never authorize device work."""
    import psutil

    try:
        with connect(address(root)) as connection:
            pid = server_pid(connection)
            if not trusted_server(pid, root):
                return None
            connection.send_bytes(b"status-v1")
            if not connection.poll(0.3):
                return None
            value = json.loads(connection.recv_bytes(LIMIT))
            if (
                not isinstance(value, dict)
                or value.get("schema") != 1
                or value.get("pid") != pid
                or value.get("tunnel") not in STATES
                or not isinstance(value.get("renewal"), str)
                or len(value["renewal"]) > 600
                or type(value.get("heartbeat_age")) not in (float, int)
                or not 0 <= value["heartbeat_age"] < 45
            ):
                return None
            return value
    except OSError, EOFError, ValueError, TypeError, psutil.Error:
        return None


def listener(name):
    from multiprocessing.connection import PipeListener
    import pywintypes
    import win32file
    import win32pipe
    import win32security
    from iphone_detector.bridge_service import current_sid

    class OwnerListener(PipeListener):
        def _new_handle(self, first=False):
            attributes = pywintypes.SECURITY_ATTRIBUTES()
            attributes.SECURITY_DESCRIPTOR = (
                win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                    "D:P(A;;GA;;;SY)(A;;GA;;;" + current_sid() + ")", 1
                )
            )
            flags = win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED
            if first:
                flags |= 0x00080000  # FILE_FLAG_FIRST_PIPE_INSTANCE
            handle = win32pipe.CreateNamedPipe(
                self._address,
                flags,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | 0x8,
                # PIPE_REJECT_REMOTE_CLIENTS: status is only available on this PC.
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                LIMIT,
                LIMIT,
                0,
                attributes,
            )
            return handle.Detach()

    return OwnerListener(name)


class StatusServer:
    def __init__(self, root, renewal):
        self.name = address(root)
        self.renewal = renewal
        self.current = ("connecting", time.monotonic())
        self.stopping = threading.Event()
        self.pipe = None
        self.thread = None

    def update(self, value):
        if value not in STATES:
            raise ValueError("Unknown tunnel status")
        self.current = (value, time.monotonic())

    def __enter__(self):
        import pywintypes

        try:
            self.pipe = listener(self.name)
        except OSError, pywintypes.error:
            LOG.warning("Live status channel unavailable; retaining file-based status.")
            return self
        self.thread = threading.Thread(target=self.serve, daemon=True, name="live-status")
        self.thread.start()
        return self

    def serve(self):
        while not self.stopping.is_set():
            try:
                with self.pipe.accept() as connection:
                    if self.stopping.is_set():
                        break
                    if not connection.poll(0.3) or connection.recv_bytes(32) != b"status-v1":
                        continue
                    renewal = self.renewal()
                    value, heartbeat = self.current
                    snapshot = {
                        "schema": 1,
                        "pid": os.getpid(),
                        "tunnel": value,
                        "heartbeat_age": max(0, time.monotonic() - heartbeat),
                        "renewal": renewal[:600],
                    }
                    connection.send_bytes(json.dumps(snapshot).encode())
            except OSError, EOFError, ValueError, TypeError:
                if not self.stopping.is_set():
                    LOG.debug("Live status request could not be completed.", exc_info=True)

    def __exit__(self, *_):
        self.stopping.set()
        if self.pipe is None:
            return
        try:
            with connect(self.name):
                pass  # Wake accept without sending a command or changing service state.
        except OSError:
            pass
        self.thread.join(timeout=1)
        self.pipe.close()


def display_snapshot(root, connection, renewal):
    """Use live status for presentation only and explicitly report discrepancies."""
    from iphone_detector.remote_tunnel import describe_state

    live = query(root)
    if live is None:
        return connection, renewal, ""
    live_connection = describe_state(live["tunnel"])
    note = ""
    if (connection, renewal) != (live_connection, live["renewal"]):
        note = " Live status recovered from the running service; local status files differ."
    return live_connection, live["renewal"], note
