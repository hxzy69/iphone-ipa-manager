"""Publish only tagged, verified artifacts; never replace a public release's bytes."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def gh(*arguments):
    return subprocess.check_output(["gh", *arguments], text=True, encoding="utf-8").strip()


def installer_assets(version):
    name = f"iPhoneIPAManager-{version}-setup-x64.exe"
    checksum = ROOT / "artifacts/SETUP-SHA256SUMS.txt"
    try:
        digest, saved_name = checksum.read_text(encoding="ascii").split()
        content = (ROOT / "artifacts" / name).read_bytes()
        if saved_name != name or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Setup checksum mismatch")
    except OSError, ValueError:
        raise ValueError("Build and verify the matching setup EXE before publishing.") from None
    return [name, checksum.name]


def publish(tag, repository, *, make_public=False):
    from iphone_detector import __version__
    from iphone_detector.release_integrity import verify_release

    if tag != f"v{__version__}-preview" or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("Choose the matching preview tag and a GitHub owner/repository.")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    target = subprocess.check_output(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    if head != target:
        raise ValueError("The checked-out source does not match the release tag.")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=ROOT, text=True
    ).strip():
        raise ValueError(
            "Release source has uncommitted changes. Build the clean tag before publishing."
        )
    verify_release(ROOT / "artifacts")
    names = [
        f"iPhoneIPAManager-{__version__}-windows-x64.zip",
        f"iPhoneIPAManager-{__version__}-source.zip",
        "SHA256SUMS.txt",
    ]
    names += installer_assets(__version__)
    existing = json.loads(
        gh("release", "list", "--repo", repository, "--limit", "100", "--json", "tagName,isDraft")
    )
    found = next((value for value in existing if value["tagName"] == tag), None)
    if found and not found["isDraft"]:
        raise ValueError(
            "This release is already public. Its artifacts are immutable; use a new version."
        )
    files = [str(ROOT / "artifacts" / name) for name in names]
    if found:
        gh("release", "upload", tag, *files, "--repo", repository, "--clobber")
    else:
        gh(
            "release",
            "create",
            tag,
            *files,
            "--repo",
            repository,
            "--target",
            head,
            "--title",
            f"iPhone IPA Manager {__version__} Preview",
            "--notes-file",
            str(ROOT / "CHANGELOG.md"),
            "--draft",
        )
    uploaded = json.loads(gh("release", "view", tag, "--repo", repository, "--json", "assets"))[
        "assets"
    ]
    for name in names:
        expected = "sha256:" + hashlib.sha256((ROOT / "artifacts" / name).read_bytes()).hexdigest()
        if not any(
            a["name"] == name and a.get("digest") == expected and a.get("state") == "uploaded"
            for a in uploaded
        ):
            raise ValueError("Uploaded asset digest did not match: " + name)
    if make_public:
        gh(
            "release",
            "edit",
            tag,
            "--repo",
            repository,
            "--draft=false",
            "--prerelease=false",
            "--latest",
        )
        for name in names:
            url = f"https://github.com/{repository}/releases/download/{tag}/{name}"
            with urlopen(Request(url, method="HEAD"), timeout=60) as response:
                if (
                    response.status != 200
                    or int(response.headers["Content-Length"])
                    != (ROOT / "artifacts" / name).stat().st_size
                ):
                    raise ValueError("Public download verification failed: " + name)
    print("Published verified preview." if make_public else "Verified draft ready; not public.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    publish(args.tag, args.repo, make_public=args.publish)
