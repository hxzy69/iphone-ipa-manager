"""Authenticate execution channels against the saved pair, not discovery alone."""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pymobiledevice3.exceptions import ConnectionTerminatedError
from pymobiledevice3.lockdown import TcpLockdownClient, UsbmuxLockdownClient
from pymobiledevice3.service_connection import ServiceConnection


class PairedTLSTests(unittest.TestCase):
    def test_certificate_rejection_falls_back_before_any_device_work(self):
        from iphone_detector import connection_router

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": "phone"}}
            ),
            patch(
                "iphone_detector.background.make_job",
                return_value={"serial": "phone", "accounts": [{"account": 1}]},
            ),
            patch.object(connection_router, "find_usb", return_value=None),
            patch.object(connection_router, "find_local", return_value="192.168.1.10"),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch("iphone_detector.remote_tunnel.enabled", return_value=False),
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                side_effect=ssl.SSLCertVerificationError("mismatch"),
            ),
            patch("iphone_detector.remote_tunnel.execute_job", new_callable=AsyncMock) as execute,
            patch(
                "iphone_detector.remote_tunnel.submit", return_value={"transport": "remote"}
            ) as submit,
        ):
            job = Path(directory)
            self.assertEqual(connection_router.run_refresh(job, Mock()), {"transport": "remote"})
            execute.assert_not_awaited()
            submit.assert_called_once()
            self.assertFalse((job / "remote-execution.json").exists())

    def client(self):
        client = TcpLockdownClient(
            AsyncMock(),
            host_id="host",
            hostname="127.0.0.1",
            pair_record={"HostID": "host", "SystemBUID": "buid", "DeviceCertificate": b"saved"},
        )
        client.all_values = {"ProductVersion": "18.0"}
        client._request = AsyncMock(return_value={"SessionID": "session", "EnableSessionSSL": True})
        client.get_value = AsyncMock(return_value={"UniqueDeviceID": "phone"})
        client.ssl_file = Mock(return_value=nullcontext("host.pem"))
        client.areestablish_connection = AsyncMock()
        return client

    def test_session_passes_saved_pin_and_rejects_failure_without_reconnect(self):
        async def check():
            client = self.client()
            self.assertTrue(await client.validate_pairing())
            client.service.ssl_start.assert_awaited_once_with("host.pem", expected_peer=b"saved")
            client.service.ssl_start.side_effect = ConnectionTerminatedError()
            with self.assertRaises(ConnectionTerminatedError):
                await client.validate_pairing()
            client.areestablish_connection.assert_not_awaited()
            client.service.close.assert_awaited_once()

        asyncio.run(check())

    def test_plaintext_session_is_rejected_before_reading_identity(self):
        async def check():
            client = self.client()
            client._request.return_value = {"SessionID": "session", "EnableSessionSSL": False}
            with self.assertRaises(ssl.SSLCertVerificationError):
                await client.validate_pairing()
            client.get_value.assert_not_awaited()
            client.service.close.assert_awaited_once()

        asyncio.run(check())

    def test_each_service_is_pinned_and_failed_connection_is_closed(self):
        async def check():
            client = self.client()
            service = AsyncMock()
            client.create_service_connection = AsyncMock(return_value=service)
            client.get_service_connection_attributes = AsyncMock(
                return_value={"Port": 1234, "EnableServiceSSL": True}
            )
            for name in (
                "com.apple.mobile.installation_proxy",
                "com.apple.misagent",
                "com.apple.afc",
            ):
                service.ssl_start.reset_mock()
                self.assertIs(await client.start_lockdown_service(name), service)
                service.ssl_start.assert_awaited_once_with("host.pem", expected_peer=b"saved")
            service.ssl_start.side_effect = ConnectionTerminatedError()
            with self.assertRaises(ConnectionTerminatedError):
                await client.start_lockdown_service("com.apple.misagent")
            service.close.assert_awaited_once()
            client.get_service_connection_attributes.return_value = {"Port": 1234}
            client.create_service_connection.reset_mock()
            with self.assertRaises(ssl.SSLCertVerificationError):
                await client.start_lockdown_service("com.apple.afc")
            client.create_service_connection.assert_not_awaited()

        asyncio.run(check())

    def test_missing_pin_is_rejected_and_bridge_network_requires_tls(self):
        client = self.client()
        client.pair_record = {}
        with self.assertRaises(ssl.SSLCertVerificationError):
            client.paired_device_certificate()
        network = UsbmuxLockdownClient.__new__(UsbmuxLockdownClient)
        network.service = Mock(mux_device=Mock(connection_type="Network"))
        self.assertTrue(network.requires_paired_tls)
        network.service.mux_device.connection_type = "USB"
        self.assertFalse(network.requires_paired_tls)

    def test_real_tls_handshake_accepts_only_saved_certificate(self):
        """Exercise both an existing-stream upgrade and a socket-first handshake."""
        with tempfile.TemporaryDirectory() as directory:
            certfile = Path(directory) / "server.pem"
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "paired-test")])
            now = datetime.now(timezone.utc)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .sign(key, hashes.SHA256())
            )
            pem = certificate.public_bytes(serialization.Encoding.PEM)
            certfile.write_bytes(
                pem
                + key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )
            other = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .sign(key, hashes.SHA256())
                .public_bytes(serialization.Encoding.PEM)
            )

            async def check():
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certfile)

                async def handler(reader, writer):
                    try:
                        await reader.read()
                    finally:
                        writer.close()

                server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
                async with server:
                    port = server.sockets[0].getsockname()[1]
                    for stream_first in (False, True):
                        for expected, accepted in ((pem, True), (other, False)):
                            sock = socket.socket()
                            sock.setblocking(False)
                            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
                            connection = ServiceConnection(sock)
                            try:
                                if stream_first:
                                    await connection.start()
                                if accepted:
                                    await connection.ssl_start(
                                        str(certfile), expected_peer=expected
                                    )
                                    self.assertIsNotNone(connection.writer)
                                else:
                                    with self.assertRaises(ConnectionTerminatedError):
                                        await connection.ssl_start(
                                            str(certfile), expected_peer=expected
                                        )
                                    self.assertIsNone(connection.writer)
                            finally:
                                await connection.close()

            asyncio.run(asyncio.wait_for(check(), 15))
