"""Release checks must stay read-only and accept only official release links."""

import json
import socket
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from iphone_detector import updates


def metadata(tag="v9.0.0"):
    return {
        "tag_name": tag,
        "html_url": f"{updates.REPOSITORY}/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
    }


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.multiple(updates, _cached=None, _cached_at=0.0)
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def response(self, payload):
        response = Mock()
        response.geturl.return_value = updates.API_URL
        response.read.return_value = payload
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_numeric_comparison_does_not_offer_downgrade(self):
        self.assertTrue(updates.Release("0.10.0", "", "0.9.0").available)
        self.assertFalse(updates.Release("0.9.0", "", "0.10.0").available)
        self.assertFalse(updates.Release("0.1.1-preview", "", "0.1.1").available)

    def test_release_link_must_match_exact_repository_and_tag(self):
        for url in (
            "http://github.com/hxzy69/iphone-ipa-manager/releases/tag/v9.0.0",
            "https://example.com/download.exe",
            f"{updates.REPOSITORY}/releases/tag/v8.0.0",
            f"{updates.REPOSITORY}/releases/tag/v9.0.0?redirect=elsewhere",
        ):
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                updates.parse_release(dict(metadata(), html_url=url))

    def test_invalid_releases_are_rejected(self):
        for value in (
            [],
            None,
            {},
            dict(metadata(), draft=True),
            dict(metadata(), prerelease=True),
            metadata("v1.2.3/../../file"),
            metadata("v1.2.3-beta"),
            metadata("v01.2.3"),
        ):
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                updates.parse_release(value)

    def test_requests_are_bounded_without_authorization_and_success_is_cached(self):
        response = self.response(json.dumps(metadata()).encode())
        with patch.object(updates, "urlopen", return_value=response) as request:
            first = updates.check_updates()
            self.assertIs(updates.check_updates(), first)
        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(args[0].full_url, updates.API_URL)
        self.assertNotIn("Authorization", args[0].headers)
        self.assertEqual(kwargs["timeout"], 10)
        response.read.assert_called_once_with(updates.MAX_RESPONSE + 1)

    def test_cache_expires(self):
        with patch.object(
            updates, "urlopen", return_value=self.response(json.dumps(metadata()).encode())
        ) as request:
            updates.check_updates()
            with patch.object(updates.time, "monotonic", return_value=updates._cached_at + 301):
                updates.check_updates()
        self.assertEqual(request.call_count, 2)

    def test_failed_check_can_be_retried_immediately(self):
        with patch.object(updates, "urlopen", side_effect=socket.timeout("private endpoint")):
            with self.assertRaises(updates.UpdateError) as result:
                updates.check_updates()
        self.assertNotIn("private endpoint", str(result.exception))
        with patch.object(
            updates, "urlopen", return_value=self.response(json.dumps(metadata()).encode())
        ):
            self.assertTrue(updates.check_updates().available)

    def test_http_failures_have_actionable_messages(self):
        for code, message in (
            (403, "request limit"),
            (429, "request limit"),
            (404, "No public release"),
            (500, "Try again"),
        ):
            with (
                self.subTest(code=code),
                patch.object(
                    updates, "urlopen", side_effect=HTTPError(updates.API_URL, code, "", {}, None)
                ),
            ):
                with self.assertRaisesRegex(updates.UpdateError, message):
                    updates.check_updates()

    def test_invalid_or_oversized_response_is_rejected(self):
        for payload in (b"bad-json", b"\xff", b"x" * (updates.MAX_RESPONSE + 1)):
            with (
                self.subTest(size=len(payload)),
                patch.object(updates, "urlopen", return_value=self.response(payload)),
            ):
                with self.assertRaises(updates.UpdateError):
                    updates.check_updates()

    def test_redirect_response_is_rejected(self):
        response = self.response(json.dumps(metadata()).encode())
        response.geturl.return_value = "https://example.com"
        with patch.object(updates, "urlopen", return_value=response):
            with self.assertRaises(updates.UpdateError):
                updates.check_updates()

    def test_runtime_version_matches_package_version(self):
        from pathlib import Path
        import tomllib
        from iphone_detector import __version__

        project = Path(__file__).resolve().parents[1] / "pyproject.toml"
        self.assertEqual(
            __version__, tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
        )
