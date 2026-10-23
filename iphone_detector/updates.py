"""Read public release metadata without credentials or changes to running workers."""

from dataclasses import dataclass
import json
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from iphone_detector import __version__

REPOSITORY = "https://github.com/hxzy69/iphone-ipa-manager"
API_URL = "https://api.github.com/repos/hxzy69/iphone-ipa-manager/releases/latest"
MAX_RESPONSE = 1024 * 1024
_lock = threading.Lock()
_cached = None
_cached_at = 0.0


class UpdateError(RuntimeError):
    """A user-readable failure that does not include local or network details."""


def version_tuple(value):
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-preview)?", value)
    if not match:
        raise ValueError("Unrecognized release version")
    return tuple(map(int, match.groups()))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    installed: str = __version__

    @property
    def available(self):
        return version_tuple(self.version) > version_tuple(self.installed)

    def summary(self):
        if self.available:
            return f"Version {self.version} is available. You are running {self.installed}."
        return f"You are running {self.installed}. Latest published version: {self.version}."


def parse_release(value):
    if not isinstance(value, dict):
        raise UpdateError("GitHub returned an invalid release response. Try again later.")
    tag = value.get("tag_name")
    url = value.get("html_url")
    try:
        version_tuple(tag)
    except ValueError, TypeError:
        raise UpdateError("The published release has an unrecognized version.") from None
    if value.get("draft") is not False or value.get("prerelease") is not False:
        raise UpdateError("No supported public release is available.")
    if url != f"{REPOSITORY}/releases/tag/{tag}":
        raise UpdateError("The release link does not match the official repository.")
    return Release(tag.removeprefix("v"), url)


def check_updates():
    """Check on request; cache successes for five minutes across Discord requests."""
    global _cached, _cached_at
    with _lock:
        if _cached is not None and time.monotonic() - _cached_at < 300:
            return _cached
        request = Request(
            API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"iPhoneIPAManager/{__version__}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                if response.geturl() != API_URL:
                    raise UpdateError("GitHub redirected the release check. Try again later.")
                payload = response.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise UpdateError("GitHub returned an oversized release response.")
            release = parse_release(json.loads(payload))
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise UpdateError("GitHub's request limit was reached. Try again later.") from None
            if exc.code == 404:
                raise UpdateError("No public release was found. Try again later.") from None
            raise UpdateError(
                "GitHub could not complete the release check. Try again later."
            ) from None
        except URLError, OSError, TimeoutError:
            raise UpdateError(
                "Could not reach GitHub. Check your connection and try again."
            ) from None
        except ValueError, UnicodeError:
            raise UpdateError("GitHub returned an unreadable release response.") from None
        _cached, _cached_at = release, time.monotonic()
        return release
