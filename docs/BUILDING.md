# Building on Windows

Use Windows 11 x64, Python 3.14 x64, Rust stable with the MSVC target, and Visual
Studio Build Tools with **Desktop development with C++**. The service helper uses
the .NET Framework C# compiler supplied by Windows. Other operating systems and
Python versions have not been validated for this distribution.

This is a PyInstaller application, not a pip-distributed library. After building
the companions, run source with `python ipa_manager.py`; use the standalone EXE
for end users. `pip install .` is not the installation method.

## Clean build

Run from the repository root, in an ordinary PowerShell window:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\tools\build.ps1
```

The script builds the Rust helper with `Cargo.lock`, compiles the fixed-purpose
service, downloads netmuxd if missing and verifies both its archive hash and
executable hash, runs tests, then packages the app. It does not install a service,
register startup tasks, contact Apple, or operate a device. It stops on any failed
command. A network connection is needed to fetch dependencies.

Outputs are in `dist/`: the standalone EXE, offline guide, third-party license
texts, dependency inventories and a self-test receipt. `tools/package_release.py`
creates versioned Windows and source ZIP files in `artifacts/`, each with SHA-256
checksums. The source ZIP comes from an allowlist; local state, binaries, build
caches, private configuration and VCS history are excluded.

If the native and service binaries are already built and verified:

```powershell
.\tools\build.ps1 -SkipNative
```

`-SkipNative` does not prove those components match edited source. Release builds
should compile them normally. The build is pinned and repeatable by procedure;
byte-for-byte reproducible EXEs have not been established. Builds are unsigned.
Windows SmartScreen reputation checks are separate from UAC; the program does
not disable either Windows protection.

## Checks during development

```powershell
.\.venv\Scripts\python.exe tools/test.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
cargo fmt --manifest-path native/Cargo.toml --check
.\.venv\Scripts\python.exe tools/audit_release.py
```

`tools/test.py` isolates application state and the roaming profile. It uses mocks
for device and Discord operations and never registers real scheduled tasks.
Native protocol tests reject invalid credentials before network access.

The Python lock records the tested Windows dependency versions. To update it,
create a disposable environment, resolve the reviewed direct requirements, then
run `tools/lock_dependencies.py`. Review the resulting diff and run all checks.
Do not regenerate locks casually as part of a normal build.

## Release checklist

- Run the complete build and inspect `dist/self-test.json`.
- Review `docs/VALIDATION.md`; do not carry forward device claims from another build.
- Inspect the source archive and generated license inventory.
- Test onboarding on a clean Windows user, then USB, Wi-Fi, account verification,
  remote roaming, scheduled catch-up and expiry fallback with a consenting device.
- Verify all configured apps launch after installation and after the previous
  provisioning profiles expire.
- Publish the archives and checksums only after those device checks are recorded.


## Rebuilding the LGPL signing component

The native Cargo manifest patches `apple-codesign-quick` to the accompanying
`vendor/apple-codesign-quick/` source. Modify that source, then run the complete
`tools/build.ps1` command (without `-SkipNative`) to compile and relink it into the
native helper and package the application. The exact application sources,
`Cargo.lock`, GPL transport sources, and build scripts accompany the Windows
release in `source/iPhoneIPAManager-0.1.1-source.zip`. No signature or application
check prevents using a modified, rebuilt helper.


## Verify a downloaded release

Place the Windows ZIP, source ZIP and `SHA256SUMS.txt` in one folder, then run:

```powershell
python tools/verify_release.py path/to/downloads
```

This checks archive integrity, paths, the EXE self-test hash and matching embedded
source without running the downloaded EXE. A checksum establishes consistency
with the checksum file; it does not authenticate an untrusted publisher. Download
all three files from this project's GitHub release.

Packaging also rejects stale self-test receipts before creating archives. The
archive version comes from `pyproject.toml`, and verification runs automatically
after packaging.
