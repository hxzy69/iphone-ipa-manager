"""Check companion provenance and reject embedded developer-profile paths."""

import hashlib
import os

from fetch_netmuxd import EXECUTABLE_SHA256, ROOT


def main():
    bridge = ROOT / "tools/netmuxd/netmuxd.exe"
    if hashlib.sha256(bridge.read_bytes()).hexdigest() != EXECUTABLE_SHA256:
        raise ValueError("The bridge executable does not match the pinned release.")
    for relative in (
        "tools/optimized-iloader/iloader-wifi.exe",
        "tools/wifi-service/BridgeService.exe",
    ):
        binary = (ROOT / relative).read_bytes()
        if not binary.startswith(b"MZ"):
            raise ValueError("Missing Windows companion executable: " + relative)
        profile = os.environ.get("USERPROFILE")
        if profile and any(
            profile.encode(encoding) in binary for encoding in ("utf-8", "utf-16le")
        ):
            raise ValueError(
                "A companion exposes the build profile path; rebuild with tools/build.ps1."
            )
    print("Companion integrity and build-path checks passed.")


if __name__ == "__main__":
    main()
