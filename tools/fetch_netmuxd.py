"""Fetch the pinned bridge archive and verify its executable before installation."""

import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_SHA256 = "8a926491603237a911150c2e05850cb3a6cb52f6b869c7049a0f91e5cf61bb80"


def fetch():
    destination = ROOT / "tools/netmuxd/netmuxd.exe"
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != EXECUTABLE_SHA256:
            raise ValueError("Existing netmuxd does not match the pinned executable.")
        return
    source = json.loads((destination.parent / "source.json").read_text(encoding="utf-8"))
    request = urllib.request.Request(source["url"], headers={"User-Agent": "IPA-Manager-Build"})
    with urllib.request.urlopen(request, timeout=90) as response:
        archive = response.read(128 * 1024 * 1024 + 1)
    if len(archive) > 128 * 1024 * 1024:
        raise ValueError("Bridge download exceeded the size limit.")
    if hashlib.sha256(archive).hexdigest() != source["archive_sha256"]:
        raise ValueError("Bridge archive checksum mismatch.")
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        entries = [name for name in package.namelist() if Path(name).name == "netmuxd.exe"]
        if len(entries) != 1:
            raise ValueError("Bridge archive must contain exactly one netmuxd executable.")
        binary = package.read(entries[0])
    if hashlib.sha256(binary).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError("Bridge executable checksum mismatch.")
    temporary = destination.with_suffix(".download")
    temporary.write_bytes(binary)
    temporary.replace(destination)


if __name__ == "__main__":
    fetch()
