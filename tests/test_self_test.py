import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_detector import self_test


class RuntimeSelfTestTests(unittest.TestCase):
    def test_source_runtime_checks_pass_without_device_access(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.remote_tunnel.submit") as submit,
        ):
            path = Path(directory) / "report.json"
            self.assertEqual(self_test.run(path), 0)
            self.assertTrue(json.loads(path.read_text())["ok"])
            submit.assert_not_called()

    def test_missing_dependency_fails_self_test(self):
        real = self_test.importlib.import_module

        def load(name):
            if name == "discord":
                raise ImportError("simulated missing runtime dependency")
            return real(name)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(self_test.importlib, "import_module", side_effect=load),
        ):
            path = Path(directory) / "report.json"
            self.assertEqual(self_test.run(path), 1)
            report = json.loads(path.read_text())
            self.assertFalse(report["ok"])
            self.assertEqual(report["checks"][0]["error_type"], "ImportError")
