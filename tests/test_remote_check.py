import unittest
from unittest.mock import Mock, MagicMock, patch
from iphone_detector import remote_check as remote


def peer(ip="100.64.0.10"):
    return {"OS": "iOS", "Online": True, "TailscaleIPs": [ip]}


class RemoteCheckTests(unittest.TestCase):
    def test_selects_ios_case_and_rejects_unlisted_target(self):
        status = {"BackendState": "Running", "Peer": {"one": peer()}}
        self.assertEqual(remote.select_peer(status), "100.64.0.10")
        with self.assertRaises(RuntimeError):
            remote.select_peer(status, "192.168.1.7")
        with self.assertRaises(RuntimeError):
            remote.select_peer(status, "100.64.0.11")

    def test_ambiguous_phone_requires_explicit_selection(self):
        status = {"BackendState": "Running", "Peer": {"a": peer(), "b": peer("100.64.0.11")}}
        with self.assertRaises(RuntimeError):
            remote.select_peer(status)
        self.assertEqual(remote.select_peer(status, "100.64.0.11"), "100.64.0.11")

    def test_offline_or_signed_out_is_not_a_success(self):
        for status in (
            {"BackendState": "NeedsLogin"},
            {"BackendState": "Running", "Peer": {"one": dict(peer(), Online=False)}},
        ):
            with self.assertRaises(RuntimeError):
                remote.select_peer(status)

    def test_wrong_tls_certificate_is_rejected_and_closed(self):
        context = Mock()
        context.wrap_socket.return_value.getpeercert.return_value = b"wrong certificate"
        with patch.object(remote.ssl, "PEM_cert_to_DER_cert", return_value=b"expected"):
            with self.assertRaisesRegex(RuntimeError, "certificate does not match"):
                remote.pinned(context, Mock(), {"DeviceCertificate": b"certificate"})
        context.wrap_socket.return_value.close.assert_called_once()

    def test_unencrypted_session_never_checks_installation_service(self):
        with (
            patch.object(remote.socket, "create_connection", return_value=MagicMock()),
            patch.object(remote, "lockdown", return_value={"EnableSessionSSL": False}) as request,
        ):
            with self.assertRaisesRegex(RuntimeError, "authenticated TLS"):
                remote.authenticate_direct(
                    "100.64.0.10", "phone", {"HostID": "host", "SystemBUID": "buid"}
                )
            self.assertEqual(request.call_count, 1)
