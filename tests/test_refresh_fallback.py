import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from iphone_detector.remote_tunnel import require_full_archive


class RefreshFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_real_archive_before_returning(self):
        with tempfile.TemporaryDirectory() as root:
            job = Path(root)
            (job / "remote-1.profiles").touch()
            native = asyncio.create_task(asyncio.sleep(20))
            try:
                with patch("iphone_detector.remote_tunnel.cancelled", return_value=False):
                    waiter = asyncio.create_task(require_full_archive(job, 1, native))
                    await asyncio.sleep(0.03)
                    self.assertTrue((job / "remote-1.full-install").exists())
                    self.assertFalse(waiter.done())
                    (job / "remote-1.full-ready").touch()
                    await asyncio.wait_for(waiter, 1)
            finally:
                native.cancel()
                await asyncio.gather(native, return_exceptions=True)

    async def test_native_exit_cannot_release_metadata_archive_for_installation(self):
        with tempfile.TemporaryDirectory() as root:
            job = Path(root)
            (job / "remote-2.profiles").touch()
            native = asyncio.create_task(asyncio.sleep(0))
            await native
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                await require_full_archive(job, 2, native)

    async def test_regular_signed_archive_needs_no_handshake(self):
        with tempfile.TemporaryDirectory() as root:
            await require_full_archive(Path(root), 3, None)
            self.assertFalse((Path(root) / "remote-3.full-install").exists())
