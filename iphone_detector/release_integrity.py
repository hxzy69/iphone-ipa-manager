"""Verify downloaded release archives without executing their contents."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile
import stat


def verify_receipt(receipt, executable, expected_version=None):
    if not isinstance(receipt, dict):
        raise ValueError("Self-test receipt must be a JSON object.")
    checks = receipt.get("checks")
    if (
        receipt.get("ok") is not True
        or receipt.get("frozen") is not True
        or not isinstance(checks, list)
        or not checks
        or any(not isinstance(item, dict) or item.get("ok") is not True for item in checks)
    ):
        raise ValueError("A passing packaged self-test with successful checks is required.")
    if receipt.get("exe_sha256") != hashlib.sha256(executable).hexdigest():
        raise ValueError("Self-test receipt does not match the executable.")
    if expected_version is not None and receipt.get("version") != expected_version:
        raise ValueError("Self-test runtime version does not match the release version.")


def verify_members(archive):
    names = archive.namelist()
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError("Archive contains duplicate member names.")
    if (
        len(names) > 25000
        or sum(member.file_size for member in archive.infolist()) > 512 * 1024 * 1024
    ):
        raise ValueError("Archive exceeds the supported expanded size.")
    for member in archive.infolist():
        # On Windows ZipInfo normalizes backslashes in filename; retain the raw name.
        name = member.orig_filename
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or any(part in ("", ".") for part in name.removesuffix("/").split("/"))
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or any(ord(char) < 32 for char in name)
            or stat.S_ISLNK(member.external_attr >> 16)
            or member.flag_bits & 1
        ):
            raise ValueError("Archive contains an unsafe member path.")
        for part in path.parts:
            stem = part.split(".")[0].upper()
            if part.endswith((".", " ")) or stem in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{n}" for n in range(1, 10)),
                *(f"LPT{n}" for n in range(1, 10)),
            }:
                raise ValueError("Archive contains an unsafe Windows filename.")
    if archive.testzip() is not None:
        raise ValueError("Archive CRC verification failed.")


def verify_release(directory):
    directory = Path(directory)
    files = {}
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, name = line.split()
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or ":" in name
            or name in files
        ):
            raise ValueError("Invalid or duplicate checksum entry.")
        content = (directory / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Archive checksum mismatch: " + name)
        files[name] = content
    windows = [name for name in files if name.endswith("-windows-x64.zip")]
    if len(files) != 2 or len(windows) != 1:
        raise ValueError("Expected one Windows archive and its matching source archive.")
    source = windows[0].removesuffix("-windows-x64.zip") + "-source.zip"
    if source not in files:
        raise ValueError("Matching source archive is missing.")
    with zipfile.ZipFile(io.BytesIO(files[source])) as archive:
        verify_members(archive)
    with zipfile.ZipFile(io.BytesIO(files[windows[0]])) as archive:
        verify_members(archive)
        verify_receipt(
            json.loads(archive.read("self-test.json")), archive.read("iPhoneIPAManager.exe")
        )
        if archive.read("source/" + source) != files[source]:
            raise ValueError("Embedded source differs from the accompanying source archive.")
    return {"ok": True, "archives": sorted(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="Folder containing the ZIPs and SHA256SUMS.txt"
    )
    print(json.dumps(verify_release(parser.parse_args().directory), indent=2))
