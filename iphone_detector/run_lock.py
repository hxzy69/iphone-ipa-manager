"""One installation at a time, shared by GUI and Discord workers."""

import ctypes
import threading
from ctypes import wintypes

_owned_names = set()
_names_guard = threading.Lock()


class RunLock:
    def __init__(self):
        self.handle = None
        self.owned = False
        self.name = "Local\\iPhoneIPAManagerInstallation"

    def acquire(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if self.handle:
            return self.owned
        # Windows mutexes are recursive for their owning thread. Two independent
        # controllers in this process must still exclude one another.
        with _names_guard:
            if self.name in _owned_names:
                return False
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        self.handle = kernel.CreateMutexW(None, False, self.name)
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        if not self.handle:
            return False
        # WAIT_ABANDONED also grants ownership after a process crash. No wait/retry:
        # callers must never silently queue another installation.
        self.owned = kernel.WaitForSingleObject(self.handle, 0) in (0, 0x80)
        if not self.owned:
            self.close()
            return False
        with _names_guard:
            _owned_names.add(self.name)
        return True

    def close(self):
        if self.handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            if self.owned:
                kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
                kernel.ReleaseMutex(self.handle)
                with _names_guard:
                    _owned_names.discard(self.name)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.handle)
            self.handle = None
            self.owned = False
