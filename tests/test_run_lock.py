import ctypes
from ctypes import wintypes
import subprocess
import sys
import unittest
import uuid

from iphone_detector.run_lock import RunLock


class RunLockTests(unittest.TestCase):
    def test_exclusion_and_release_across_processes(self):
        name = "Local\\IPAManagerTest-" + uuid.uuid4().hex
        first = RunLock()
        first.name = name
        code = (
            "import sys;from iphone_detector.run_lock import RunLock;"
            "lock=RunLock();lock.name=sys.argv[1];print(lock.acquire());lock.close()"
        )
        try:
            self.assertTrue(first.acquire())
            blocked = subprocess.check_output(
                [sys.executable, "-c", code, name], text=True, timeout=10
            ).strip()
            self.assertEqual(blocked, "False")
            first.close()
            released = subprocess.check_output(
                [sys.executable, "-c", code, name], text=True, timeout=10
            ).strip()
            self.assertEqual(released, "True")
        finally:
            first.close()

    def test_existing_but_unowned_mutex_can_be_acquired(self):
        name = "Local\\IPAManagerTest-" + uuid.uuid4().hex
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        external = kernel.CreateMutexW(None, False, name)
        lock = RunLock()
        lock.name = name
        try:
            self.assertTrue(lock.acquire())
            lock.close()
            # The external handle keeps the named object alive after ownership releases.
            self.assertTrue(lock.acquire())
        finally:
            lock.close()
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(external)
