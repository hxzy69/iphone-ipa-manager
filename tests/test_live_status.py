import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from iphone_detector import live_status as live


class LiveStatusTests(unittest.TestCase):
    def test_live_channel_overrides_stale_files_without_writing_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stale = root / "remote-state.json"
            stale.write_text('{"state":"offline"}')
            with live.StatusServer(root, lambda: "Due tomorrow.") as server:
                server.update("ready")
                with patch.object(
                    live, "trusted_server", side_effect=lambda pid, _: pid == os.getpid()
                ):
                    connection, renewal, note = live.display_snapshot(
                        root, "Offline.", "Due yesterday."
                    )
                self.assertEqual(connection, "Remote tunnel ready.")
                self.assertEqual(renewal, "Due tomorrow.")
                self.assertIn("recovered", note)
                self.assertEqual(stale.read_text(), '{"state":"offline"}')
            self.assertFalse(server.thread.is_alive())
            self.assertIsNone(live.query(root))

    def test_unknown_peer_and_stale_heartbeat_are_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with live.StatusServer(root, lambda: "Due tomorrow.") as server:
                server.update("ready")
                with patch.object(live, "trusted_server", return_value=False):
                    self.assertIsNone(live.query(root))
                server.current = ("ready", time.monotonic() - 60)
                with patch.object(live, "trusted_server", return_value=True):
                    self.assertIsNone(live.query(root))

    def test_untrusted_command_cannot_change_state_and_server_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with live.StatusServer(root, lambda: "Due tomorrow.") as server:
                server.update("ready")
                with live.connect(server.name) as client:
                    client.send_bytes(b"restart-service")
                    self.assertTrue(client.poll(1))
                    with self.assertRaises(EOFError):
                        client.recv_bytes()
                with patch.object(live, "trusted_server", return_value=True):
                    self.assertEqual(live.query(root)["tunnel"], "ready")

    def test_profile_and_executable_identity_are_checked(self):
        import sys

        root = Path.cwd() / "profile"
        process = Mock()
        process.exe.return_value = sys.executable
        process.cmdline.return_value = [sys.executable, "--remote-daemon", "--data-dir", str(root)]
        with patch("psutil.Process", return_value=process):
            self.assertTrue(live.trusted_server(123, root))
            self.assertFalse(live.trusted_server(123, root / "another"))
            process.exe.return_value = str(root / "another.exe")
            self.assertFalse(live.trusted_server(123, root))

    def test_malformed_or_oversized_reply_is_not_displayed(self):
        base = {"schema": 1, "pid": 123, "tunnel": "ready", "heartbeat_age": 0, "renewal": "Due."}
        for replacement in (
            {"heartbeat_age": float("nan")},
            {"renewal": "x" * 601},
            {"pid": 999},
            {"tunnel": []},
        ):
            with self.subTest(replacement=replacement):
                connection = Mock()
                connection.poll.return_value = True
                connection.recv_bytes.return_value = json.dumps(base | replacement).encode()
                context = MagicMock()
                context.__enter__.return_value = connection
                with (
                    patch.object(live, "connect", return_value=context),
                    patch.object(live, "server_pid", return_value=123),
                    patch.object(live, "trusted_server", return_value=True),
                ):
                    self.assertIsNone(live.query(Path.cwd()))

    def test_unavailable_channel_keeps_existing_status(self):
        with patch.object(live, "query", return_value=None):
            self.assertEqual(
                live.display_snapshot(Path.cwd(), "Connecting.", "Due."),
                ("Connecting.", "Due.", ""),
            )
