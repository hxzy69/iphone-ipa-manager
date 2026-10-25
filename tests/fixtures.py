"""Explicit sample accounts for tests that exercise an existing installation."""

from copy import deepcopy
import plistlib

ACCOUNTS = [
    {"email": "account1@example.com", "ipa": "SampleVideo 1.0"},
    {"email": "account2@example.com", "ipa": "SampleAudio 1.0"},
    {"email": "account3@example.com", "ipa": "SampleStream 1.0"},
]


def configured(values=None):
    result = deepcopy(values or {})
    result.setdefault("automation", {}).setdefault("accounts", deepcopy(ACCOUNTS))
    return result


def write_sample_app(archive, metadata="Payload/Test.app/Info.plist"):
    archive.writestr(
        metadata,
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.example.sample",
                "CFBundleExecutable": "Sample",
                "CFBundleVersion": "1",
                "CFBundleShortVersionString": "1.0",
            }
        ),
    )
    archive.writestr(metadata.rsplit("/", 1)[0] + "/Sample", b"sample executable")
