import asyncio
from contextlib import asynccontextmanager
import ipaddress
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from iphone_detector import connection_router as router, remote_tunnel as remote


class LocalTunnelTests(unittest.TestCase):
    def test_daemon_never_claims_locally_owned_request(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
        ):
            job = Path(folder) / "automation-runs/local"
            job.mkdir(parents=True)
            remote.atomic(job / "remote-request.json", {"accounts": [1], "execution": "local"})
            self.assertEqual(list(remote.pending()), [])
            self.assertFalse((job / "remote-result.json").exists())

    def run_local(self, job, *, preflight=False, execution=False):
        @asynccontextmanager
        async def session(*args):
            if preflight:
                raise OSError("listener unavailable")
            yield Mock()

        async def perform(path, client):
            request = json.loads((path / "remote-request.json").read_text())
            self.assertEqual(request["execution"], "local")
            self.assertEqual(request["accounts"], [2])
            if execution:
                raise OSError("connection lost after submission")
            remote.atomic(path / "remote-result.json", {"ok": True, "completed_accounts": 1})

        stop = Mock()
        stop.cancelled.is_set.return_value = False
        with (
            patch("iphone_detector.config.load_config", return_value={}),
            patch(
                "iphone_detector.background.make_job",
                return_value={"serial": "phone", "accounts": [{"account": 2}]},
            ),
            patch.object(remote, "settings", return_value={"serial": "phone"}),
            patch.object(
                router, "local_networks", return_value=[ipaddress.ip_network("192.168.1.0/24")]
            ),
            patch.object(remote, "open_session", side_effect=session),
            patch.object(remote, "execute_job", side_effect=perform),
        ):
            return router.run_local_tunnel(job, stop, "192.168.1.7", accounts=[1], refresh=True)

    def test_local_tunnel_preserves_selection_and_verified_result(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.run_local(Path(folder))["transport"], "local-wifi")
            self.assertTrue((Path(folder) / "refresh-request").exists())

    def test_only_preflight_failure_is_classified_for_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(router.LocalRefreshUnavailable):
                self.run_local(Path(folder), preflight=True)
            self.assertFalse((Path(folder) / "remote-request.json").exists())

    def test_failure_after_submission_is_never_classified_for_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(OSError) as raised:
                self.run_local(Path(folder), execution=True)
            self.assertNotIsInstance(raised.exception, router.LocalRefreshUnavailable)
            self.assertTrue((Path(folder) / "remote-request.json").exists())

    def test_transient_service_failures_preserve_live_roaming_transport(self):
        async def run():
            future = asyncio.get_running_loop().create_future()
            client = Mock(_ipa_transport_watch=future)

            @asynccontextmanager
            async def session(*args):
                yield client

            states = []
            clock = itertools.count(0, 20)
            with (
                patch.object(remote, "open_session", side_effect=session),
                patch.object(remote.subprocess, "check_output", return_value=b"{}"),
                patch("iphone_detector.remote_check.select_peer", return_value="100.64.0.10"),
                patch.object(remote, "enabled", side_effect=[True] * 5 + [False]),
                patch.object(remote, "pending", return_value=[]),
                patch.object(
                    remote,
                    "lookup",
                    new_callable=AsyncMock,
                    side_effect=[OSError(), OSError(), OSError(), {}, {}],
                ) as lookup,
                patch.object(remote, "time", Mock(monotonic=lambda: next(clock))),
                patch.object(remote.asyncio, "sleep", new_callable=AsyncMock),
            ):
                await remote.connected({"ip": "100.64.0.10"}, states.append)
            self.assertEqual(lookup.await_count, 5)
            self.assertIn("connecting", states)
            self.assertEqual(states[-1], "ready")
            self.assertFalse(future.done())

        asyncio.run(run())
