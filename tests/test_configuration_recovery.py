"""Malformed persisted preferences recover before GUI construction or device work."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from iphone_detector import config, maintenance, discord_setup


class ConfigurationRecoveryTests(unittest.TestCase):
    def test_inaccessible_settings_are_not_mistaken_for_an_empty_new_profile(self):
        path = Mock()
        # Filesystem existence probes can suppress access errors. The read itself
        # must distinguish missing settings from inaccessible existing settings.
        path.exists.return_value = False
        path.read_text.side_effect = PermissionError("fixture access denied")
        with patch.object(config, "CONFIG_PATH", path):
            with self.assertRaises(config.ConfigurationError):
                config.load_config()
        path.read_text.assert_called_once()

    def test_malformed_supported_fields_are_preserved_and_can_be_quarantined(self):
        invalid = (
            {"device": []},
            {"device": {"serial": 123}},
            {"automation": []},
            {"automation": {"install_timeout": "900"}},
            {"automation": {"install_timeout": float("nan")}},
            {"automation": {"install_timeout": True}},
            {"automation": {"notify_on_finish": "false"}},
            {"automation": {"ipa_directory": None}},
            {"automation": {"pending_ipa_installs": []}},
            {"automation": {"pending_ipa_installs": {"0": "Sample"}}},
            {"automation": {"accounts": [{"email": "fixture@example.com", "ipa": None}]}},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            for value in invalid:
                with self.subTest(value=value), patch.object(config, "CONFIG_PATH", path):
                    original = json.dumps(value).encode()
                    path.write_bytes(original)
                    with self.assertRaises(config.ConfigurationError):
                        config.load_config()
                    self.assertEqual(path.read_bytes(), original)
                    saved = maintenance.quarantine_invalid_config(root)
                    self.assertEqual(saved.read_bytes(), original)
                    self.assertFalse(path.exists())

    def test_invalid_save_does_not_modify_file_or_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_bytes(b'{"keep":"existing"}')
            invalid = {"device": {"serial": "fixture"}, "automation": {"install_timeout": "bad"}}
            original = copy.deepcopy(invalid)
            with patch.object(config, "CONFIG_PATH", path):
                with self.assertRaises(ValueError):
                    config.save_config(invalid)
            self.assertEqual(path.read_bytes(), b'{"keep":"existing"}')
            self.assertEqual(invalid, original)

    def test_missing_directories_and_unknown_preferences_do_not_prevent_repair(self):
        value = {
            "device": {},
            "automation": {
                "ipa_directory": "unavailable-folder",
                "future_preference": {"keep": True},
            },
            "other": {"keep": True},
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"),
        ):
            config.save_config(copy.deepcopy(value))
            self.assertEqual(config.load_config(), value)

    def test_discord_listener_can_start_without_a_legacy_wifi_service(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(discord_setup, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.bridge_service.installed", return_value=False) as bridge,
            patch.object(discord_setup, "command", return_value=["fixture.exe", "--discord"]),
            patch.object(discord_setup.subprocess, "Popen", return_value=Mock(pid=123)) as launch,
        ):
            discord_setup.start_listener()
            bridge.assert_not_called()
            launch.assert_called_once()
            self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
