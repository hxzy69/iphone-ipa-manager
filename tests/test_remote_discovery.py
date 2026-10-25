import asyncio
import base64
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from iphone_detector import remote_discovery as discovery
from pymobiledevice3.bonjour import encode_name
from pymobiledevice3.remote.siphash import compute_auth_tag


IDENTIFIER = "TEST-PHONE"
ADDRESS = "192.168.1.25"
KEY = bytes(range(16))
SERVICE = "test._remotepairing._tcp.local."


def record(name, kind, payload, ttl=120):
    return encode_name(name) + struct.pack("!HHIH", kind, 1, ttl, len(payload)) + payload


def reply(tag=None, transaction=42, port=50000, ttl=120):
    tag = tag or base64.b64encode(compute_auth_tag(KEY, "test-instance")).decode()
    properties = [b"identifier=test-instance", ("authTag=" + tag).encode()]
    txt = b"".join(bytes([len(item)]) + item for item in properties)
    srv = record(SERVICE, 33, struct.pack("!HHH", 0, 0, port) + encode_name("phone.local."), ttl)
    return struct.pack("!6H", transaction, 0x8400, 0, 2, 0, 0) + srv + record(SERVICE, 16, txt)


class RemoteDiscoveryTests(unittest.TestCase):
    def query(self, packet=None, sender=(ADDRESS, 5353), records=None):
        stream = MagicMock()
        stream.recvfrom.return_value = (packet if packet is not None else reply(), sender)
        with (
            patch.object(discovery.socket, "socket") as factory,
            patch.object(discovery.secrets, "randbelow", return_value=41),
            patch(
                "pymobiledevice3.pair_records.iter_remote_pair_records_by_identifier",
                return_value=records
                if records is not None
                else [(IDENTIFIER, None, {"peer_alt_irk": KEY})],
            ),
        ):
            factory.return_value.__enter__.return_value = stream
            result = discovery.query_local(ADDRESS, IDENTIFIER)
        return result, stream

    def test_accepts_authenticated_tag_and_queries_only_selected_phone(self):
        answers, stream = self.query()
        self.assertEqual([answer.port for answer in answers], [50000])
        self.assertEqual(answers[0].addresses[0].ip, ADDRESS)
        self.assertEqual(stream.sendto.call_args.args[1], (ADDRESS, 5353))
        stream.settimeout.assert_called_once_with(3)

    def test_rejects_forged_authentication_tag(self):
        self.assertEqual(self.query(reply(tag=base64.b64encode(b"xxxxxx").decode()))[0], [])

    def test_rejects_unrelated_sender_and_transaction(self):
        self.assertEqual(self.query(sender=("192.168.1.99", 5353))[0], [])
        self.assertEqual(self.query(reply(transaction=43))[0], [])

    def test_rejects_expired_records_and_invalid_port(self):
        self.assertEqual(self.query(reply(ttl=0))[0], [])
        self.assertEqual(self.query(reply(port=0))[0], [])

    def test_rejects_truncated_and_oversized_record_headers(self):
        self.assertEqual(self.query(b"short")[0], [])
        self.assertEqual(self.query(struct.pack("!6H", 42, 0x8400, 0, 257, 0, 0))[0], [])

    def test_missing_pair_record_sends_nothing(self):
        answers, stream = self.query(records=[])
        self.assertEqual(answers, [])
        stream.sendto.assert_not_called()

    def test_never_queries_public_loopback_or_multicast_addresses(self):
        for address in ("8.8.8.8", "127.0.0.1", "224.0.0.251", "169.254.1.2"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                discovery.query_local(address, IDENTIFIER)

    def test_multicast_success_does_not_attempt_fallback(self):
        with (
            patch("iphone_detector.remote_check.pairing_identifier", return_value=IDENTIFIER),
            patch(
                "pymobiledevice3.remote.tunnel_service.get_remote_pairing_tunnel_services",
                new_callable=AsyncMock,
                return_value=["existing"],
            ),
            patch.object(discovery, "query_local") as query,
        ):
            self.assertEqual(asyncio.run(discovery.discover(IDENTIFIER, {})), ["existing"])
            query.assert_not_called()

    def test_multicast_failure_uses_authenticated_local_endpoint_without_pairing(self):
        answer = MagicMock(port=50000)
        service = MagicMock()
        with (
            patch("iphone_detector.remote_check.pairing_identifier", return_value=IDENTIFIER),
            patch(
                "pymobiledevice3.remote.tunnel_service.get_remote_pairing_tunnel_services",
                new_callable=AsyncMock,
                side_effect=OSError,
            ),
            patch("iphone_detector.connection_router.find_local", return_value=ADDRESS),
            patch.object(discovery, "query_local", return_value=[answer]) as query,
            patch(
                "pymobiledevice3.remote.tunnel_service.create_core_device_tunnel_service_using_remotepairing",
                new_callable=AsyncMock,
                return_value=service,
            ) as connect,
        ):
            self.assertEqual(asyncio.run(discovery.discover(IDENTIFIER, {})), [service])
            query.assert_called_once_with(ADDRESS, IDENTIFIER)
            connect.assert_awaited_once_with(IDENTIFIER, ADDRESS, 50000, autopair=False)

    def test_unicast_requires_paired_local_identity_before_authentication(self):
        with (
            patch("iphone_detector.remote_check.pairing_identifier", return_value=IDENTIFIER),
            patch(
                "pymobiledevice3.remote.tunnel_service.get_remote_pairing_tunnel_services",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("iphone_detector.connection_router.find_local", return_value=None),
            patch.object(discovery, "query_local") as query,
        ):
            self.assertEqual(asyncio.run(discovery.discover(IDENTIFIER, {})), [])
            query.assert_not_called()
