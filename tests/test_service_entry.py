"""Unattended failures must produce status and exit codes, never GUI crash flow."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from iphone_detector import service_entry
from iphone_detector.job_history import read


class ServiceEntryTests(unittest.TestCase):
    def test_failures_keep_one_incident_and_never_expose_exception_in_report(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(service_entry, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.discord_outbox.enqueue", return_value=True) as enqueue,
        ):
            callback = Mock(side_effect=RuntimeError("private-test-secret"))
            for _ in range(2):
                self.assertEqual(service_entry.run("queue", callback), 1)
            first, second = enqueue.call_args_list
            self.assertEqual(first.args[1], second.args[1])
            self.assertNotIn("private-test-secret", str(first))
            status = read(Path(directory) / "queue-service.json")
            self.assertEqual(status["state"], "failed")
            self.assertNotIn("private-test-secret", str(status))

    def test_success_resolves_incident_and_a_later_failure_gets_a_new_one(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(service_entry, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.discord_outbox.enqueue", return_value=True) as enqueue,
        ):
            self.assertEqual(service_entry.run("renewal", lambda: 1), 1)
            first = enqueue.call_args.args[1]
            self.assertEqual(service_entry.run("renewal", lambda: None), 0)
            self.assertEqual(read(Path(directory) / "renewal-service.json")["state"], "stopped")
            self.assertEqual(service_entry.run("renewal", lambda: 1), 1)
            self.assertNotEqual(enqueue.call_args.args[1], first)

    def test_failure_reporting_failure_does_not_open_an_unhandled_exception(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(service_entry, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.storage.write_json", side_effect=PermissionError("fixture")),
            patch("iphone_detector.discord_outbox.enqueue", side_effect=OSError("fixture")),
        ):
            self.assertEqual(service_entry.run("remote", Mock(side_effect=OSError("fixture"))), 1)

    def test_unknown_component_cannot_create_arbitrary_paths(self):
        with self.assertRaises(ValueError):
            service_entry.run("../fixture", Mock())
