"""Content identities and private, immutable inputs for installation workers."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil


def identity(path, *, digest=True):
    path = Path(path)
    before = path.stat()
    value = {"size": before.st_size, "mtime_ns": before.st_mtime_ns}
    if digest:
        with path.open("rb") as stream:
            value["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("The IPA changed while checking it. Wait for the transfer to finish.")
    return value


@contextmanager
def snapshot_inputs(job, request):
    """Copy once, verify copied bytes, and remove only the copies owned by this call."""
    import copy
    import tempfile
    import uuid
    from iphone_detector.ipa_inbox import validate_ipa

    if request.get("check_only"):
        yield request
        return
    required = sum(a.get("file_identity", {}).get("size", 0) for a in request["accounts"])
    if shutil.disk_usage(job).free < required + 256 * 1024 * 1024:
        raise ValueError("Not enough free disk space to prepare safe installation inputs.")
    with tempfile.TemporaryDirectory(prefix="inputs-", dir=job) as directory:
        prepared = copy.deepcopy(request)
        for account in prepared["accounts"]:
            expected = account.get("file_identity", {})
            if not expected.get("sha256"):
                raise ValueError("This request has no content identity. Start a new run.")
            destination = (
                Path(directory) / f"account-{account['account']}-{uuid.uuid4().hex[:12]}.ipa"
            )
            shutil.copyfile(account["ipa"], destination)
            actual = identity(destination)
            if any(actual[key] != expected.get(key) for key in ("size", "sha256")):
                raise ValueError("The IPA changed after this run was requested. Start a new run.")
            validate_ipa(destination)
            account["ipa"] = str(destination)
        yield prepared


def https_server(value):
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        raise ValueError("Choose an HTTPS anisette server.")
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(ord(char) < 33 for char in value)
    ):
        raise ValueError(
            "Apple authentication requires an HTTPS anisette server without credentials."
        )
    return value
