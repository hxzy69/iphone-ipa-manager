from copy import deepcopy
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from iphone_detector import assignment_history, config, ipa_library, job_progress, run_review
from iphone_detector.discord_inbox import assign_managed


class ProductFeaturesTests(unittest.TestCase):
    def test_repeated_preparation_polls_replace_elapsed_suffix(self):
        (self.root / "worker.json").write_text(json.dumps({"started_at": 100, "state": "running"}))
        with patch("iphone_detector.job_progress.time.time", return_value=161):
            first = job_progress.with_elapsed(self.root, "Preparing.")
            second = job_progress.with_elapsed(self.root, first)
        self.assertEqual(second, first)
        self.assertEqual(second.count("elapsed"), 1)

    def test_queue_rechecks_review_after_content_preparation(self):
        from iphone_detector import job_queue

        review = run_review.plan(config.load_config(), accounts=[0])

        def prepare(*args, **kwargs):
            with (self.folder / "First.ipa").open("ab") as stream:
                stream.write(b"changed during preparation")
            return {"serial": "sample-phone", "accounts": []}

        with (
            patch("iphone_detector.background.make_job", side_effect=prepare),
            patch.object(job_queue, "ROOT", self.root / "queue"),
            patch.object(job_queue, "ensure_started") as start,
        ):
            with self.assertRaisesRegex(ValueError, "changed after review"):
                job_queue.enqueue("install", [0], expected_review=review)
            start.assert_not_called()
            self.assertFalse((self.root / "queue").exists())

    def test_concurrent_saves_keep_every_assignment_history_entry(self):
        from concurrent.futures import ThreadPoolExecutor

        original = config.load_config()

        def save(number):
            value = deepcopy(original)
            value["automation"]["accounts"][0]["ipa"] = "Version%d" % number
            config.save_config(value)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(save, range(10)))
        rows = assignment_history.entries(config.load_config())
        self.assertEqual(len(rows), 10)
        self.assertEqual(
            {row["current"]["ipa"] for row in rows}, {"Version%d" % n for n in range(10)}
        )

    def test_review_scope_cannot_expand_to_other_accounts_or_change_operation(self):
        value = config.load_config()
        review = run_review.plan(value, accounts=[0])
        run_review.verify_scope(review, value, "install", [0])
        with self.assertRaisesRegex(ValueError, "account selection changed"):
            run_review.verify_scope(review, value, "install")
        with self.assertRaises(ValueError):
            run_review.verify_scope(review, value, "refresh", [0])
        with self.assertRaisesRegex(ValueError, "all configured accounts"):
            run_review.plan(value, "refresh", [0])

    def test_worker_rechecks_review_before_starting_device_work(self):
        import ipa_manager

        value = config.load_config()
        value["automation"]["notify_on_finish"] = False
        config.save_config(value)
        review = run_review.plan(value, accounts=[0])
        job = self.root / "reviewed-job"
        job.mkdir()
        (job / "review-plan.json").write_text(json.dumps(review))
        with (
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.connection_router.run_automatic") as run,
        ):
            self.assertEqual(ipa_manager.worker("discord-run", job), 1)
            run.assert_not_called()
        self.assertIn(
            "account selection changed", json.loads((job / "result.json").read_text())["error"]
        )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.folder = self.root / "ipas"
        self.folder.mkdir()
        self.addCleanup(ipa_library.clear_cache)
        self.config_patch = patch.object(config, "CONFIG_PATH", self.root / "settings.json")
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.write("First", "2.9")
        self.write("Other", "1", "org.example.other")
        config.save_config(
            {
                "device": {"serial": "sample-phone"},
                "automation": {
                    "ipa_directory": str(self.folder),
                    "accounts": [
                        {"email": "one@example.com", "ipa": "First"},
                        {"email": "two@example.com", "ipa": "Other"},
                    ],
                },
            }
        )

    def write(self, name, version, bundle="org.example.app"):
        path = self.folder / (name + ".ipa")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "Payload/App.app/Info.plist",
                plistlib.dumps(
                    {
                        "CFBundleIdentifier": bundle,
                        "CFBundleExecutable": "App",
                        "CFBundleVersion": version,
                        "CFBundleShortVersionString": version,
                        "CFBundleDisplayName": "Sample app",
                    }
                ),
            )
            archive.writestr("Payload/App.app/App", b"sample binary")
        return path

    def assign(self, name, expected=None):
        path = self.folder / (name + ".ipa")
        stat = path.stat()
        return assign_managed(
            path,
            (stat.st_size, stat.st_mtime_ns),
            0,
            expected_email="one@example.com",
            expected_ipa=expected,
        )

    def test_newer_numeric_version_suggested_only_for_matching_bundle(self):
        self.write("Second", "2.10")
        self.write("Unrelated", "999", "org.example.unrelated")
        self.write("Beta", "3.0-beta")
        value = config.load_config()
        from iphone_detector.settings import get_workflow

        suggestions = ipa_library.updates(ipa_library.scan(value), get_workflow(value))
        self.assertEqual(list(suggestions), [0])
        self.assertEqual(suggestions[0][1].path.stem, "Second")
        self.assertIsNone(ipa_library.version_key("1e100"))
        self.assertEqual(ipa_library.version_key("2.0"), ipa_library.version_key("2"))

    def test_ambiguous_same_version_does_not_auto_choose_a_variant(self):
        self.write("Second", "2.10")
        self.write("Variant", "2.10")
        value = config.load_config()
        self.assertEqual(
            ipa_library.updates(ipa_library.scan(value), value["automation"]["accounts"]), {}
        )

    def test_history_saved_with_assignment_and_unchanged_saves_do_not_duplicate(self):
        self.write("Second", "2.10")
        self.assign("Second")
        value = config.load_config()
        rows = assignment_history.entries(value)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["previous"]["ipa"], "First")
        self.assertEqual(rows[0]["current"]["ipa"], "Second")
        config.save_config(value)
        self.assertEqual(len(assignment_history.entries(config.load_config())), 1)

    def test_undo_restores_one_ipa_and_marks_it_pending(self):
        self.write("Second", "2.10")
        self.assign("Second")
        identifier = assignment_history.entries(config.load_config())[0]["id"]
        self.assertEqual(assignment_history.undo(identifier), 1)
        value = config.load_config()
        self.assertEqual(
            [row["ipa"] for row in value["automation"]["accounts"]], ["First", "Other"]
        )
        self.assertEqual(value["automation"]["pending_ipa_installs"], {"1": "First"})
        self.assertEqual(len(assignment_history.entries(value)), 2)

    def test_undo_refuses_replaced_account_or_changed_assignment(self):
        self.write("Second", "2.10")
        self.write("Third", "3")
        self.assign("Second")
        identifier = assignment_history.entries(config.load_config())[0]["id"]
        self.assign("Third")
        with self.assertRaisesRegex(ValueError, "assignment changed"):
            assignment_history.undo(identifier)
        self.assertEqual(config.load_config()["automation"]["accounts"][0]["ipa"], "Third")

    def test_undo_refuses_missing_or_changed_previous_file(self):
        self.write("Second", "2.10")
        self.assign("Second")
        identifier = assignment_history.entries(config.load_config())[0]["id"]
        previous = self.folder / "First.ipa"
        with previous.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "file has changed"):
            assignment_history.undo(identifier)
        previous.unlink()
        with self.assertRaisesRegex(ValueError, "not in the current IPA folder"):
            assignment_history.undo(identifier)

    def test_history_is_bounded_and_drops_unknown_fields(self):
        previous = config.load_config()
        for number in range(105):
            current = deepcopy(previous)
            current["automation"]["accounts"][0]["ipa"] = "Version%d" % number
            assignment_history.record(previous, current)
            previous = current
        rows = assignment_history.entries(previous)
        self.assertEqual(len(rows), 100)
        rows[0]["previous"]["password"] = "should-not-be-retained"
        self.assertNotIn(
            "password", assignment_history.entries({"assignment_history": rows})[0]["previous"]
        )
        self.assertEqual(
            assignment_history.entries({"assignment_history": [None, {"id": "bad"}]}), []
        )

    def test_failed_write_preserves_saved_assignment_and_history(self):
        value = config.load_config()
        value["automation"]["accounts"][0]["ipa"] = "Second"
        with patch("iphone_detector.storage.write_json", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                config.save_config(value)
        saved = config.load_config()
        self.assertEqual(saved["automation"]["accounts"][0]["ipa"], "First")
        self.assertEqual(assignment_history.entries(saved), [])

    def test_stale_assignment_guard_rejects_before_archive_validation(self):
        self.write("Second", "2.10")
        with patch("iphone_detector.ipa_inbox.validate_ipa") as validate:
            with self.assertRaisesRegex(ValueError, "assignment changed"):
                self.assign("Second", expected="Wrong")
            validate.assert_not_called()

    def test_review_distinguishes_install_pending_assignment_and_profile_refresh(self):
        review = run_review.plan(config.load_config(), "refresh")
        self.assertIn("if eligible", review["rows"][0]["operation"])
        self.write("Second", "2.10")
        self.assign("Second")
        review = run_review.plan(config.load_config(), "refresh")
        self.assertEqual(review["rows"][0]["operation"], "Full installation (changed assignment)")
        self.assertEqual(run_review.verify(review, config.load_config()), review)

    def test_review_rejects_file_change_and_account_change(self):
        review = run_review.plan(config.load_config(), accounts=[0])
        with (self.folder / "First.ipa").open("ab") as stream:
            stream.write(b"change")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            run_review.verify(review, config.load_config())
        review = run_review.plan(config.load_config(), accounts=[0])
        value = config.load_config()
        value["automation"]["accounts"][0]["email"] = "replacement@example.com"
        with self.assertRaisesRegex(ValueError, "changed after review"):
            run_review.verify(review, value)

    def test_invalid_and_empty_reviews_are_rejected(self):
        for value in ({}, None, {"mode": "install", "token": "x", "rows": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_review.verify(value, config.load_config())
        with self.assertRaises(ValueError):
            run_review.plan(config.load_config(), accounts=[True])

    def test_progress_handles_partial_events_and_elapsed_time(self):
        (self.root / "backend-events.jsonl").write_text(
            json.dumps({"stage": "sign", "account": 1, "email": "private"}) + '\n{"stage":'
        )
        (self.root / "worker.json").write_text(
            json.dumps({"started_at": 100, "state": "running", "accounts": [1, 2]})
        )
        self.assertEqual(job_progress.elapsed(self.root, now=161), "1m 01s elapsed")
        value = job_progress.snapshot(self.root)
        self.assertEqual(
            value["accounts"],
            [{"account": 1, "stage": "Signing"}, {"account": 2, "stage": "Preparing"}],
        )
        self.assertNotIn("private", json.dumps(value))
        self.assertEqual(value["state"], "Working")

    def test_prepared_only_and_failed_result_are_never_completed(self):
        (self.root / "worker.json").write_text(json.dumps({"accounts": [1]}))
        for receipt in (
            {"ok": True, "completed_accounts": 1, "prepared_only": True},
            {"error": "device connection secret-value"},
        ):
            (self.root / "result.json").write_text(json.dumps(receipt))
            value = job_progress.snapshot(self.root)
            self.assertIn(value["state"], ("Needs attention", "Prepared only"))
            self.assertNotIn("secret-value", value["hint"])
        (self.root / "result.json").write_text(json.dumps({"ok": True, "completed_accounts": 1}))
        self.assertEqual(job_progress.snapshot(self.root)["state"], "Completed")
