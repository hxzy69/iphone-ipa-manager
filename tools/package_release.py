"""Create public source and Windows archives from explicit release inputs."""

import hashlib
import json
import zipfile
import tomllib

from verify_release import verify_receipt, verify_release

from audit_release import ROOT, audit, source_files

VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def archive(destination, root, paths):
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as output:
        for path in sorted(paths):
            output.write(path, path.relative_to(root).as_posix())


def main():
    report = audit()
    if not report["ok"]:
        raise ValueError("Source audit failed; inspect tools/audit_release.py output.")
    dist = ROOT / "dist"
    receipt = json.loads((dist / "self-test.json").read_text(encoding="utf-8"))
    verify_receipt(receipt, (dist / "iPhoneIPAManager.exe").read_bytes())
    files = [
        dist / name
        for name in (
            "iPhoneIPAManager.exe",
            "START_HERE.html",
            "LICENSE.txt",
            "NOTICE.md",
            "self-test.json",
            "dependencies.json",
            "native-dependencies.json",
        )
    ]
    files += [path for path in (dist / "licenses").rglob("*") if path.is_file()]
    if any(not path.is_file() for path in files):
        raise ValueError(
            "Release inputs are missing. Complete the build and license collection first."
        )
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    source = output / f"iPhoneIPAManager-{VERSION}-source.zip"
    windows = output / f"iPhoneIPAManager-{VERSION}-windows-x64.zip"
    archive(source, ROOT, source_files())
    accompanying_source = dist / "source" / source.name
    accompanying_source.parent.mkdir(exist_ok=True)
    accompanying_source.write_bytes(source.read_bytes())
    files.append(accompanying_source)
    archive(windows, dist, files)
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in (source, windows)
    )
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")
    (output / "source-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    verify_release(output)
    print("Created source and Windows archives; checksums are in artifacts/SHA256SUMS.txt.")


if __name__ == "__main__":
    main()
