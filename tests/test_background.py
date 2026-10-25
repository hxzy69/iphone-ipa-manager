from fixtures import configured
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from iphone_detector import background
from fixtures import ACCOUNTS as DEFAULT_ACCOUNTS


class BackgroundTests(unittest.TestCase):
    def test_truthy_and_untyped_backend_results_cannot_claim_verified_completion(self):
        values = (
            [],
            {"ok": 1, "completed_accounts": 1},
            {"ok": True, "completed_accounts": True},
            {"ok": True, "completed_accounts": 1.0},
        )
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            request = {"accounts": [{"account": 1}]}
            process = Mock(returncode=0, poll=Mock(return_value=0))
            for value in values:
                (job / "backend-result.json").write_text(json.dumps(value))
                with (
                    self.subTest(value=value),
                    patch.object(background, "cache_executable", return_value=Path("backend.exe")),
                    patch.object(background.subprocess, "Popen", return_value=process),
                ):
                    with self.assertRaises(RuntimeError):
                        background.run_background(job, Mock(), request_override=request)

    def test_damaged_progress_does_not_abort_a_successful_native_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "backend-result.json").write_text(
                json.dumps({"ok": True, "completed_accounts": 1})
            )
            (job / "backend-events.jsonl").write_text(
                'broken\n[]\n{}\n{"stage":"completed_account","account":1}\n'
            )
            process = Mock(returncode=0)
            process.poll.side_effect = [None, 0, 0]
            with (
                patch.object(background, "cache_executable", return_value=Path("backend.exe")),
                patch.object(background.subprocess, "Popen", return_value=process),
            ):
                result = background.run_background(
                    job, Mock(), request_override={"accounts": [{"account": 1}]}
                )
            self.assertTrue(result["ok"])
            process.terminate.assert_not_called()

    def setUp(self):
        # These tests isolate backend result interpretation. Snapshot integrity
        # is exercised with real archives in test_input_hardening.
        from contextlib import nullcontext

        item = patch(
            "iphone_detector.input_integrity.snapshot_inputs",
            side_effect=lambda job, request: nullcontext(request),
        )
        item.start()
        self.addCleanup(item.stop)

    def test_selection_snapshot_rejects_changed_account_or_file(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            request = {
                "serial": "phone",
                "accounts": [
                    {
                        "account": 2,
                        "email": "two@example.com",
                        "ipa": "Chosen.ipa",
                        "file_identity": {"size": 1, "mtime_ns": 1, "sha256": "0" * 64},
                    }
                ],
            }
            (job / "selection.json").write_text(json.dumps(request))
            with patch.object(background, "make_job", return_value=request):
                background.validate_selection(job, {}, [1])
            for current in (
                {"serial": "other", "accounts": request["accounts"]},
                {
                    "serial": "phone",
                    "accounts": [{"account": 2, "email": "two@example.com", "ipa": "Other.ipa"}],
                },
            ):
                with patch.object(background, "make_job", return_value=current):
                    with self.assertRaisesRegex(ValueError, "changed"):
                        background.validate_selection(job, {}, [1])

    def test_unicode_error_summary_excludes_source_paths(self):
        text = "\n ● Account 1 login:\n │ ● User action required: complete Apple verification\n ╰ src/background.rs:112\n"
        self.assertEqual(
            background.error_summary(text),
            "Account 1 login: User action required: complete Apple verification",
        )

    def test_job_reads_selected_anisette_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferences = root / "me.nabdev.iloader/preferences.json"
            preferences.parent.mkdir()
            preferences.write_text(json.dumps({"newAnisetteServer": "mac-ani.jkcoxson.com"}))
            for account in DEFAULT_ACCOUNTS:
                (root / (account["ipa"] + ".ipa")).write_bytes(b"ipa")
            config = configured(
                {"device": {"serial": "phone"}, "automation": {"ipa_directory": str(root)}}
            )
            with patch.dict(os.environ, APPDATA=str(root)):
                self.assertEqual(
                    background.make_job(config, "Network")["anisette_server"],
                    "https://mac-ani.jkcoxson.com",
                )

    def test_job_uses_exact_files_and_order_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for account in DEFAULT_ACCOUNTS:
                (root / (account["ipa"] + ".ipa")).write_bytes(b"ipa")
            config = configured(
                {"device": {"serial": "saved-phone"}, "automation": {"ipa_directory": str(root)}}
            )
            request = background.make_job(config, "Network")
            self.assertEqual(
                [a["email"] for a in request["accounts"]], [a["email"] for a in DEFAULT_ACCOUNTS]
            )
            self.assertTrue(all(Path(a["ipa"]).is_absolute() for a in request["accounts"]))
            self.assertNotIn("password", json.dumps(request))
            self.assertEqual(request["parent_pid"], os.getpid())
            self.assertTrue(request["fast"])

    def test_missing_file_fails_before_starting_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            config = configured(
                {"device": {"serial": "saved-phone"}, "automation": {"ipa_directory": directory}}
            )
            with self.assertRaisesRegex(ValueError, "IPA file not found"):
                background.make_job(config, "USB")

    def test_single_account_job_selects_only_that_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for account in DEFAULT_ACCOUNTS[1:]:
                (root / (account["ipa"] + ".ipa")).write_bytes(b"ipa")
            config = configured(
                {"device": {"serial": "saved-phone"}, "automation": {"ipa_directory": str(root)}}
            )
            request = background.make_job(config, "Network", accounts=[1])
            self.assertEqual([entry["account"] for entry in request["accounts"]], [2])
            self.assertEqual(request["accounts"][0]["email"], DEFAULT_ACCOUNTS[1]["email"])

    def test_account_selection_normalizes_validates_and_rejects(self):
        workflow = [object(), object(), object()]
        self.assertEqual(background.selected_accounts(workflow, None), [0, 1, 2])
        self.assertEqual(background.selected_accounts(workflow, [2, 0, 0]), [0, 2])
        for bad in ([3], [-1], [1.0], ["1"], [], [None]):
            with self.assertRaises(ValueError):
                background.selected_accounts(workflow, bad)

    def test_backend_success_requires_all_selected_completions(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "backend-result.json").write_text(
                json.dumps({"ok": True, "completed_accounts": 1})
            )
            process = Mock(returncode=0)
            process.poll.return_value = 0
            stop = Mock()
            request = {"accounts": [{"account": 1}, {"account": 3}]}
            with (
                patch.object(background, "make_job", return_value=request),
                patch.object(background, "cache_executable", return_value=Path("backend.exe")),
                patch.object(background.subprocess, "Popen", return_value=process),
            ):
                with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
                    background.run_background(job, stop, request_override=request)

    def test_backend_success_requires_all_three_verified_completions(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "backend-result.json").write_text(
                json.dumps({"ok": True, "completed_accounts": 2})
            )
            process = Mock(returncode=0)
            process.poll.return_value = 0
            stop = Mock()
            with (
                patch.object(background, "make_job", return_value=configured()),
                patch.object(background, "cache_executable", return_value=Path("backend.exe")),
                patch.object(background.subprocess, "Popen", return_value=process),
            ):
                with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
                    background.run_background(job, stop)

    def test_signing_export_requires_explicit_backend_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "backend-result.json").write_text(
                json.dumps({"ok": True, "completed_accounts": 0})
            )
            process = Mock(returncode=0)
            process.poll.return_value = 0
            with (
                patch.object(background, "make_job", return_value={"prepare_only": True}),
                patch.object(background, "cache_executable", return_value=Path("backend.exe")),
                patch.object(background.subprocess, "Popen", return_value=process),
            ):
                with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
                    background.run_background(job, Mock(), "Remote")


if __name__ == "__main__":
    unittest.main()
