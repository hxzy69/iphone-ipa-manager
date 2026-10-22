from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_detector.storage import write_json


class StorageTests(unittest.TestCase):
    def test_concurrent_writers_leave_a_complete_document_and_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"

            def write(index):
                write_json(path, {"index": index, "payload": str(index) * 2000})

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(30)))
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["payload"], str(result["index"]) * 2000)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_serialization_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"keep": True})
            with self.assertRaises(TypeError):
                write_json(path, {"bad": object()})
            self.assertEqual(json.loads(path.read_text()), {"keep": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_persistent_reader_contention_preserves_previous_file_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"keep": True})
            with (
                patch.object(Path, "replace", side_effect=PermissionError),
                patch("iphone_detector.storage.time.sleep"),
            ):
                with self.assertRaises(PermissionError):
                    write_json(path, {"keep": False})
            self.assertEqual(json.loads(path.read_text()), {"keep": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
