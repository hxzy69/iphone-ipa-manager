"""Support snapshots must never serialize raw settings, jobs or exceptions."""

import json
import unittest
from unittest.mock import patch

from iphone_detector.diagnostics import report


class DiagnosticsTests(unittest.TestCase):
    def test_component_exit_history_exports_only_known_states(self):
        for state, expected in (
            ("failed", "failed"),
            ("stopped", "stopped"),
            ("private-value", "unknown"),
            ({"private": "value"}, "unknown"),
        ):
            with (
                self.subTest(state=state),
                patch(
                    "iphone_detector.job_history.read",
                    return_value={
                        "state": state,
                        "incident": "private-value",
                        "path": "private-value",
                    },
                ),
            ):
                value = report()
            self.assertEqual(set(value["last_service_exits"].values()), {expected})
            self.assertNotIn("private-value", json.dumps(value))

    def test_snapshot_uses_only_health_flags(self):
        private = "PRIVATE-value-never-export"
        with (
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": private}}
            ),
            patch(
                "iphone_detector.settings.get_workflow",
                return_value=[{"email": private, "ipa": private}],
            ),
            patch(
                "iphone_detector.remote_tunnel.state",
                return_value={"state": "ready", "ip": private, "serial": private},
            ),
            patch("iphone_detector.worker_registry.active_jobs", return_value=[{"job": private}]),
            patch(
                "iphone_detector.auto_refresh.schedule_snapshot",
                return_value=(
                    {"enabled": True, "anchor": 1},
                    {"at": 1, "job": private},
                    {"pending_job": private},
                ),
            ),
        ):
            value = report()
        self.assertNotIn(private, json.dumps(value))
        self.assertEqual(value["configured_accounts"], 1)
        self.assertEqual(value["active_jobs"], 1)
        self.assertEqual(value["remote_tunnel"], "ready")
        self.assertTrue(value["renewal_overdue"])
        self.assertTrue(value["renewal_pending"])

    def test_corrupt_inputs_preserve_other_sections_and_hide_exception_details(self):
        with (
            patch(
                "iphone_detector.config.load_config", side_effect=ValueError("private-file-path")
            ),
            patch("iphone_detector.remote_tunnel.state", side_effect=OSError("private-ip")),
            patch(
                "iphone_detector.worker_registry.active_jobs", side_effect=OSError("private-job")
            ),
            patch(
                "iphone_detector.auto_refresh.schedule_snapshot",
                side_effect=ValueError("private-config"),
            ),
        ):
            value = report()
        self.assertNotIn("private", json.dumps(value))
        for key in ("configuration", "remote_tunnel", "active_jobs", "automatic_renewal"):
            self.assertEqual(value[key], "unreadable")
        self.assertIn("version", value)

    def test_unexpected_tunnel_text_is_not_exported(self):
        with (
            patch("iphone_detector.config.load_config", return_value={}),
            patch("iphone_detector.settings.get_workflow", return_value=[]),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "private-value"}),
            patch("iphone_detector.worker_registry.active_jobs", return_value=[]),
            patch("iphone_detector.auto_refresh.schedule_snapshot", return_value=({}, {}, {})),
        ):
            value = report()
        self.assertEqual(value["remote_tunnel"], "unknown")
        self.assertIsNone(value["renewal_overdue"])
        self.assertEqual(value["automatic_renewal"], "disabled")
