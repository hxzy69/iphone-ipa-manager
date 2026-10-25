"""Shared onboarding findings, profile isolation and early-expiry support reporting."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_detector import readiness, paths


class ReadinessTests(unittest.TestCase):
    def test_real_checklist_window_loads_off_the_ui_thread(self):
        import threading
        import time
        import tkinter as tk
        from iphone_detector.gui import App

        window = tk.Tk()
        window.withdraw()
        app = App.__new__(App)
        app.root = window
        release = threading.Event()
        calls = []
        main = threading.get_ident()

        def inspect():
            calls.append(threading.get_ident())
            release.wait(3)
            return {
                "checks": [
                    {
                        "status": "ok",
                        "title": "Settings",
                        "detail": "Checklist fixture loaded",
                        "action": "",
                    }
                ]
            }

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        try:
            with patch.object(readiness, "inspect", side_effect=inspect):
                start = time.monotonic()
                app.open_checklist()
                self.assertLess(time.monotonic() - start, 0.5)
                release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    window.update()
                    texts = [
                        item.get("1.0", "end")
                        for item in widgets(window)
                        if isinstance(item, tk.Text)
                    ]
                    if any("Checklist fixture loaded" in text for text in texts):
                        break
                    time.sleep(0.01)
                self.assertTrue(any("Checklist fixture loaded" in text for text in texts))
                self.assertTrue(calls)
                self.assertNotEqual(calls[0], main)
        finally:
            release.set()
            window.destroy()

    def test_tunnel_singleton_is_scoped_to_its_profile_without_exposing_the_path(self):
        with patch.object(paths, "DATA_ROOT", Path(self.temp.name) / "profile-one"):
            first = paths.profile_lock_name("RemoteTunnel")
            self.assertEqual(first, paths.profile_lock_name("RemoteTunnel"))
            self.assertNotIn(self.temp.name, first)
        with patch.object(paths, "DATA_ROOT", Path(self.temp.name) / "profile-two"):
            self.assertNotEqual(first, paths.profile_lock_name("RemoteTunnel"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(readiness, "DATA_ROOT", self.root))
        self.stack.enter_context(patch("iphone_detector.config.load_config", return_value={}))
        self.stack.enter_context(
            patch("iphone_detector.auto_refresh.schedule_snapshot", return_value=({}, {}, {}))
        )
        self.stack.enter_context(patch("iphone_detector.job_queue.entries", return_value=[]))
        self.stack.enter_context(
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "offline"})
        )

    def test_empty_setup_has_actionable_steps_and_no_false_device_readiness(self):
        value = readiness.inspect()
        self.assertTrue(value["needs_attention"])
        self.assertFalse(value["device_accessed"])
        rows = {row["code"]: row for row in value["checks"]}
        for code in ("device", "accounts", "files"):
            self.assertEqual(rows[code]["status"], "error")
            self.assertTrue(rows[code]["action"])
        self.assertLess(len(readiness.format_report(value)), 1950)

    def test_private_values_and_exception_details_never_enter_the_checklist(self):
        private = "private-owner-and-path-value"
        with (
            patch(
                "iphone_detector.config.load_config",
                return_value={
                    "device": {"serial": private},
                    "automation": {
                        "ipa_directory": str(self.root),
                        "accounts": [{"email": private + "@example.com", "ipa": private}],
                    },
                },
            ),
            patch(
                "iphone_detector.auto_refresh.schedule_snapshot", side_effect=ValueError(private)
            ),
            patch("iphone_detector.job_queue.entries", side_effect=ValueError(private)),
        ):
            value = readiness.inspect()
        self.assertNotIn(private, json.dumps(value))
        codes = {row["code"] for row in value["checks"]}
        self.assertIn("remote", codes)
        self.assertIn("backend", codes)

    def test_profile_matching_never_treats_default_process_as_custom_profile(self):
        default = Path(os.environ["LOCALAPPDATA"]) / "iPhoneIPAManagerCommunity"
        custom = self.root / "custom"
        self.assertFalse(paths.process_uses_profile(["app.exe", "--discord"], custom))
        self.assertTrue(paths.process_uses_profile(["app.exe", "--discord"], default))
        for args in (
            ["app.exe", "--data-dir", str(custom)],
            ["app.exe", "--data-dir=" + str(custom)],
        ):
            self.assertTrue(paths.process_uses_profile(args, custom))
            self.assertFalse(paths.process_uses_profile(args, default))
        for args in (
            ["--data-dir"],
            ["--data-dir", "relative"],
            ["--data-dir", str(custom), "--data-dir", str(custom)],
        ):
            self.assertFalse(paths.process_uses_profile(args, custom))

    def test_diagnostics_respect_observed_profile_expiry_deadline(self):
        from iphone_detector.diagnostics import report

        with (
            patch(
                "iphone_detector.auto_refresh.schedule_snapshot",
                return_value=(
                    {"enabled": True, "anchor": 1000},
                    {"at": 1000, "next_due": 1100},
                    {},
                ),
            ),
            patch("iphone_detector.diagnostics.time.time", return_value=1200),
            patch("iphone_detector.worker_registry.active_jobs", return_value=[]),
        ):
            self.assertTrue(report()["renewal_overdue"])
