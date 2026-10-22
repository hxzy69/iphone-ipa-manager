"""Collect installed dependency notices without exposing local installation paths."""

from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def is_notice(path):
    return any(
        part.upper().startswith(("LICENSE", "COPYING", "COPYRIGHT", "NOTICE"))
        for part in path.parts
    )


def copy_notices(paths, destination):
    destination.mkdir(parents=True, exist_ok=True)
    names = []
    for index, path in enumerate(sorted(set(paths))):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower()
            not in {
                ".py",
                ".pyc",
                ".pyo",
                ".pyi",
                ".pyd",
                ".dll",
                ".exe",
                ".so",
                ".c",
                ".h",
            }
        ):
            data = path.read_bytes()
            profile = os.environ.get("USERPROFILE")
            if profile and any(
                profile.encode(encoding) in data for encoding in ("utf-8", "utf-16le")
            ):
                raise ValueError("A notice contains the build profile path: " + path.name)
            name = f"{index:02d}-{path.name}"
            shutil.copyfile(path, destination / name)
            names.append((destination / name).relative_to(DIST).as_posix())
    return names


def main():
    DIST.mkdir(exist_ok=True)
    licenses = DIST / "licenses"
    if licenses.exists():
        if licenses.is_symlink() or licenses.resolve().parent != DIST.resolve():
            raise ValueError("License output must be a directory immediately inside dist.")
        shutil.rmtree(licenses)
    libraries = []
    for package in sorted(metadata.distributions(), key=lambda p: p.metadata["Name"].casefold()):
        name, version = package.metadata["Name"], package.version
        paths = [Path(package.locate_file(file)) for file in package.files or [] if is_notice(file)]
        supplement = ROOT / "third_party/notices" / f"{name}-{version}"
        if not paths and supplement.is_dir():
            paths = [path for path in supplement.iterdir() if path.is_file()]
        notices = copy_notices(paths, DIST / "licenses/python" / f"{name}-{version}")
        libraries.append(
            {
                "name": name,
                "version": version,
                "license_expression": package.metadata.get("License-Expression"),
                "notices": notices,
            }
        )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        copy_notices([python_license], DIST / "licenses/python-runtime")
    write_json(
        DIST / "dependencies.json",
        {"scope": "resolved Python build environment", "packages": libraries},
    )
    graph = json.loads(
        subprocess.check_output(
            [
                "cargo",
                "metadata",
                "--locked",
                "--format-version",
                "1",
                "--manifest-path",
                str(ROOT / "native/Cargo.toml"),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    )
    crates = []
    for crate in sorted(graph["packages"], key=lambda value: (value["name"], value["version"])):
        folder = Path(crate["manifest_path"]).parent
        paths = [path for path in folder.iterdir() if path.is_file() and is_notice(path)]
        if crate.get("license_file"):
            paths.append(folder / crate["license_file"])
        supplement = ROOT / "third_party/notices" / f"{crate['name']}-{crate['version']}"
        if not paths and supplement.is_dir():
            paths = [path for path in supplement.iterdir() if path.is_file()]
        notices = copy_notices(
            paths, DIST / "licenses/rust" / f"{crate['name']}-{crate['version']}"
        )
        crates.append(
            {
                "name": crate["name"],
                "version": crate["version"],
                "license": crate.get("license"),
                "source": crate.get("source") or "local source distribution",
                "notices": notices,
            }
        )
    write_json(
        DIST / "native-dependencies.json",
        {"scope": "Cargo lock dependency graph", "packages": crates},
    )
    copy_notices(
        [ROOT / "tools/netmuxd/LICENSE", ROOT / "tools/netmuxd/source.json"],
        DIST / "licenses/netmuxd",
    )
    print(
        f"Collected notices for {len(libraries)} Python distributions and {len(crates)} Rust packages."
    )


if __name__ == "__main__":
    main()
