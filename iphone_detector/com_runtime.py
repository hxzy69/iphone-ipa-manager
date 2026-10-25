"""Balance only COM initialization owned by the current call."""

import ctypes


def ole32():
    library = ctypes.WinDLL("ole32")
    library.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    library.CoInitializeEx.restype = ctypes.c_long
    library.CoUninitialize.argtypes = []
    library.CoUninitialize.restype = None
    return library


def initialize():
    # Use the native calls as a pair. pythoncom tracks main-thread initialization
    # itself; its convenience wrapper can short-circuit a nested initialization.
    status = ole32().CoInitializeEx(None, 2) & 0xFFFFFFFF
    if status in (0, 1):  # S_OK / S_FALSE both acquire one native initialization.
        return True
    if status == 0x80010106:  # Existing MTA: do not release somebody else's context.
        return False
    raise OSError("Windows COM initialization failed (0x%08X)." % status)


def uninitialize(owned):
    if owned:
        ole32().CoUninitialize()
