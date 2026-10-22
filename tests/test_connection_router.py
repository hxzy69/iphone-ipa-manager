import ipaddress
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, AsyncMock

from iphone_detector import connection_router as router


class RouterTests(unittest.TestCase):
    def test_only_on_link_private_addresses_are_candidates(self):
        networks = [ipaddress.ip_network("192.168.1.0/24")]
        self.assertTrue(router.on_link("192.168.1.7", networks))
        for ip in ("192.168.2.7", "100.64.0.10", "8.8.8.8", "127.0.0.1", "169.254.1.2", "invalid"):
            self.assertFalse(router.on_link(ip, networks))

    def test_same_subnet_never_substitutes_for_paired_identity(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(router, "CACHE", Path(folder) / "cache.json"),
            patch.object(
                router, "local_networks", return_value=[ipaddress.ip_network("192.168.1.0/24")]
            ),
            patch.object(router, "tailscale_candidate", return_value="192.168.1.7"),
            patch.object(
                router, "paired_local_session", side_effect=RuntimeError("wrong certificate")
            ) as paired,
            patch(
                "pymobiledevice3.bonjour.browse_mobdev2", new_callable=AsyncMock, return_value=[]
            ),
        ):
            self.assertIsNone(router.find_local("phone", {}, Mock()))
            paired.assert_called_once_with("192.168.1.7", "phone")

    def test_cached_ip_is_reauthenticated_instead_of_trusted(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(router, "CACHE", Path(folder) / "cache.json"),
            patch.object(
                router, "local_networks", return_value=[ipaddress.ip_network("192.168.1.0/24")]
            ),
            patch.object(router, "tailscale_candidate", return_value=None),
            patch.object(router, "paired_local_session") as paired,
        ):
            router.CACHE.write_text(json.dumps({"ip": "192.168.1.7", "serial": "phone"}))
            self.assertEqual(router.find_local("phone", {}, Mock()), "192.168.1.7")
            paired.assert_called_once_with("192.168.1.7", "phone")

    def route(
        self,
        job,
        *,
        local=True,
        enter_error=False,
        cleanup=True,
        install_error=False,
        accounts=None,
    ):
        self.bridge = Mock()
        self.bridge.cleanup_verified = cleanup
        if enter_error:
            self.bridge.__enter__ = Mock(side_effect=RuntimeError("local unavailable"))
        else:
            self.bridge.__enter__ = Mock(return_value=self.bridge)
        self.bridge.__exit__ = Mock()
        self.native = Mock(return_value={"ok": True, "completed_accounts": 3})
        if install_error:
            self.native.side_effect = RuntimeError("install failed")
        self.remote = Mock(return_value={"ok": True, "completed_accounts": 3})
        contexts = [
            patch.object(router, "find_local", return_value="192.168.1.7" if local else None),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": "phone"}}
            ),
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.wifi.WifiBridge", return_value=self.bridge),
            patch("iphone_detector.background.run_background", self.native),
            patch("iphone_detector.remote_tunnel.submit", self.remote),
        ]
        from contextlib import ExitStack

        with ExitStack() as stack:
            for context in contexts:
                stack.enter_context(context)
            return router.run_automatic(job, Mock(), accounts)

    def test_authenticated_local_uses_original_fast_backend_and_releases_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.route(Path(folder))
            self.assertEqual(result["transport"], "local-wifi")
            self.assertEqual(self.native.call_args.args[2], "Network")
            self.bridge.__exit__.assert_called_once()
            self.remote.assert_not_called()

    def test_remote_used_when_phone_not_on_local_network(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.route(Path(folder), local=False)["transport"], "remote")
            self.native.assert_not_called()
            self.bridge.__enter__.assert_not_called()

    def test_clean_local_acquisition_failure_can_fall_back_before_install(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.route(Path(folder), enter_error=True)["transport"], "remote")
            self.native.assert_not_called()
            self.remote.assert_called_once()

    def test_unknown_service_cleanup_blocks_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                self.route(Path(folder), enter_error=True, cleanup=False)
            self.remote.assert_not_called()

    def test_install_failure_never_replays_run_over_remote(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                self.route(Path(folder), install_error=True)
            self.bridge.__exit__.assert_called_once()
            self.remote.assert_not_called()

    def test_account_selection_reaches_local_backend(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.route(Path(folder), accounts=[2])
            self.assertEqual(result["transport"], "local-wifi")
            self.assertEqual(self.native.call_args.kwargs.get("accounts"), [2])
            self.remote.assert_not_called()

    def test_account_selection_reaches_remote_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(
                self.route(Path(folder), local=False, accounts=[0, 2])["transport"], "remote"
            )
            self.native.assert_not_called()
            self.assertEqual(self.remote.call_args.args[2], [0, 2])
