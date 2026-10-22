import asyncio
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from iphone_detector import afc_upload
from iphone_detector.remote_tunnel import upload_streams


class FakeAfc:
    def __init__(self, data, fail=False, corrupt=False):
        self.data, self.fail, self.corrupt = data, fail, corrupt
        self.offset = 0
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def fopen(self, name, mode):
        if mode == "w":
            self.data.clear()
        return 1

    async def fclose(self, handle):
        pass

    async def fseek(self, handle, offset):
        self.offset = offset

    async def fwrite(self, handle, block):
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("writer failed")
        end = self.offset + len(block)
        self.data.extend(b"\0" * max(0, end - len(self.data)))
        self.data[self.offset : end] = block
        self.offset = end

    async def _send_and_wait(self, *args):
        return 0, b"wrong" if self.corrupt else hashlib.sha1(self.data).digest()

    async def stat(self, name):
        return {"st_size": len(self.data)}


class ParallelUploadTests(unittest.IsolatedAsyncioTestCase):
    async def run_upload(self, corrupt=False, fail=False):
        data, progress, writers = bytearray(), [], []
        source = bytes(range(251)) * 501 + b"odd"

        def service(_):
            value = FakeAfc(data, fail=fail and not writers)
            writers.append(value)
            return value

        with (
            tempfile.TemporaryDirectory() as root,
            patch("pymobiledevice3.services.afc.AfcService", side_effect=service),
            patch.object(afc_upload, "CHUNK", 4096),
        ):
            path = Path(root) / "signed.ipa"
            path.write_bytes(source)
            control = FakeAfc(data, corrupt=corrupt)
            await afc_upload.upload(None, control, path, "/owned.ipa", progress.append)
            self.assertEqual(bytes(data), source)
            self.assertEqual(sum(progress), len(source))
            self.assertTrue(all(w.closed for w in writers))

    async def test_odd_sized_file_reassembled_exactly(self):
        await self.run_upload()

    async def test_bad_hash_fails_before_installation(self):
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            await self.run_upload(corrupt=True)

    async def test_writer_failure_propagates(self):
        with self.assertRaises(ExceptionGroup):
            await self.run_upload(fail=True)


class StreamSelectionTests(unittest.TestCase):
    def test_only_public_direct_endpoint_uses_parallel_upload(self):
        for endpoint, expected in [("8.8.8.8", 2), ("192.168.1.7", 1), (None, 1)]:
            with (
                self.subTest(endpoint=endpoint),
                patch(
                    "iphone_detector.connection_router.tailscale_candidate", return_value=endpoint
                ),
            ):
                self.assertEqual(upload_streams(), expected)
