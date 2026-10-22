"""Offline tests for the pure helper functions (no Windows/WMI needed).

Run from the project root or anywhere:
    python tests/test_detector.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from iphone_detector.detector import AppleDevice, extract_pid, extract_serial, is_mobile_device


def test_extract_serial_standard():
    assert extract_serial(r"USB\VID_05AC&PID_12A8\0000810100123ABC") == "0000810100123ABC"


def test_extract_serial_wpdbusenum():
    assert (
        extract_serial(
            r"SWD\WPDBUSENUM\USB#VID_05AC&PID_12A8#0000810100123ABC&_0#{53F5630D-B6BF-11D0-94F2-00A0C91EFB8B}"
        )
        == "0000810100123ABC"
    )


def test_extract_serial_port_derived_is_none():
    assert extract_serial(r"USB\VID_05AC&PID_12A8\1&2a3b4c5d&0&2") is None


def test_extract_serial_interface_child_is_none():
    assert extract_serial(r"USB\VID_05AC&PID_12A8&MI_00\0000810100123ABC&0&0000") is None


def test_extract_serial_empty():
    assert extract_serial("") is None
    assert extract_serial(None) is None


def test_extract_pid():
    assert extract_pid(r"USB\VID_05AC&PID_12A8\X") == "12A8"
    assert extract_pid(r"USB\VID_05AC&PID_12a8\X") == "12A8"  # case-normalised
    assert extract_pid("USB\\VID_05AC\\X") is None


def test_is_mobile_device():
    assert is_mobile_device(AppleDevice("Apple iPhone", "", None, None))
    assert is_mobile_device(AppleDevice("Apple Mobile Device USB Device", "", None, None))
    assert not is_mobile_device(AppleDevice("Generic USB Hub", "", None, None))
    assert not is_mobile_device(AppleDevice("Apple Internal Keyboard", "", None, None))


def test_summary():
    dev = AppleDevice("Apple iPhone", "", "0000810100123ABC", "12A8")
    assert "12A8" in dev.summary() and "0000810100123ABC" in dev.summary()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except AssertionError as exc:
                failures += 1
                print("FAIL %s: %s" % (name, exc))
    print("All tests passed." if failures == 0 else "%d test(s) FAILED" % failures)
    sys.exit(1 if failures else 0)
