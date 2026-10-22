import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from iphone_detector import wifi


def network(serial="00000000-0000000000000001"):
    return {"DeviceID": 1, "Properties": {"SerialNumber": serial, "ConnectionType": "Network"}}


class WifiTests(unittest.TestCase):
    def test_companion_is_cached_and_corrupted_cache_is_repaired(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root / "source.exe"
            binary.write_bytes(b"local build")
            cached = wifi.cache_executable(binary, root / "cache")
            self.assertEqual(cached.read_bytes(), b"local build")
            self.assertEqual(wifi.cache_executable(binary, root / "cache"), cached)
            cached.write_bytes(b"damaged")
            self.assertEqual(
                wifi.cache_executable(binary, root / "cache").read_bytes(), b"local build"
            )

    def test_saved_usb_serial_matches_network_udid(self):
        self.assertEqual(
            wifi.select_network_device([network()], "000000000000000000000001")["DeviceID"], 1
        )

    def test_wrong_phone_and_multiple_devices_are_rejected(self):
        for items in ([network("anotherphone")], [network(), network("anotherphone")], []):
            with self.assertRaises(RuntimeError):
                wifi.select_network_device(items, "000000000000000000000001")

    def test_usb_does_not_satisfy_wireless_check(self):
        device = network()
        device["Properties"]["ConnectionType"] = "USB"
        with self.assertRaises(RuntimeError):
            wifi.select_network_device([device], device["Properties"]["SerialNumber"])

    def test_invalid_packet_size_rejected_before_read(self):
        stream = Mock()
        for size in (-1, wifi.MAX_PACKET + 1):
            with self.assertRaises(ValueError):
                wifi.exact(stream, size)
        stream.recv.assert_not_called()

    def test_helper_restores_service_when_stop_fails_partway(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            kernel = Mock()
            kernel.CreateMutexW.return_value = 1

            def change(action, state):
                if action == "stop":
                    raise RuntimeError("stop failed")

            with (
                patch.object(wifi.ctypes, "windll") as dll,
                patch.object(wifi, "process_handle", return_value=(kernel, 1)),
                patch.object(wifi.ctypes, "get_last_error", return_value=0),
                patch.object(wifi, "service_state", return_value=4),
                patch.object(wifi, "change_service", side_effect=change) as service,
            ):
                dll.shell32.IsUserAnAdmin.return_value = True
                self.assertEqual(wifi.supervise(job, 123), 1)
                self.assertEqual(
                    [c.args for c in service.call_args_list], [("stop", 1), ("start", 4)]
                )
            status = json.loads((job / "bridge-status.json").read_text())
            self.assertTrue(status["apple_service_restored"])
            self.assertEqual(status["error"], "stop failed")

    def test_helper_stops_bridge_and_restores_service_after_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            kernel = Mock()
            kernel.CreateMutexW.return_value = 1
            kernel.WaitForSingleObject.return_value = 258
            bridge = Mock()
            bridge.poll.return_value = 1
            with (
                patch.object(wifi.ctypes, "windll") as dll,
                patch.object(wifi, "process_handle", return_value=(kernel, 1)),
                patch.object(wifi.ctypes, "get_last_error", return_value=0),
                patch.object(wifi, "service_state", return_value=4),
                patch.object(wifi, "change_service") as service,
                patch.object(wifi.subprocess, "Popen", return_value=bridge),
            ):
                dll.shell32.IsUserAnAdmin.return_value = True
                self.assertEqual(wifi.supervise(job, 123), 1)
                self.assertEqual(
                    [c.args for c in service.call_args_list], [("stop", 1), ("start", 4)]
                )

    def test_controller_exit_terminates_bridge_and_restores_apple_service(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            kernel = Mock()
            kernel.CreateMutexW.return_value = 1
            kernel.WaitForSingleObject.side_effect = [258, 0]
            bridge = Mock()
            bridge.poll.return_value = None
            with (
                patch.object(wifi.ctypes, "windll") as dll,
                patch.object(wifi, "process_handle", return_value=(kernel, 1)),
                patch.object(wifi.ctypes, "get_last_error", return_value=0),
                patch.object(wifi, "service_state", return_value=4),
                patch.object(wifi, "change_service") as service,
                patch.object(wifi.subprocess, "Popen", return_value=bridge),
            ):
                dll.shell32.IsUserAnAdmin.return_value = True
                self.assertEqual(wifi.supervise(job, 123), 0)
                bridge.terminate.assert_called_once()
                bridge.wait.assert_called_once()
                self.assertEqual(
                    [c.args for c in service.call_args_list], [("stop", 1), ("start", 4)]
                )


if __name__ == "__main__":
    unittest.main()
