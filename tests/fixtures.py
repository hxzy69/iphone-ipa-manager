"""Explicit sample accounts for tests that exercise an existing installation."""

from copy import deepcopy

ACCOUNTS = [
    {"email": "account1@example.com", "ipa": "SampleVideo 1.0"},
    {"email": "account2@example.com", "ipa": "SampleAudio 1.0"},
    {"email": "account3@example.com", "ipa": "SampleStream 1.0"},
]


def configured(values=None):
    result = deepcopy(values or {})
    result.setdefault("automation", {}).setdefault("accounts", deepcopy(ACCOUNTS))
    return result
