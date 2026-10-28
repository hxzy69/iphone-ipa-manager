# iPhone IPA Manager

[**Visit the project website →**](https://hxzy69.github.io/iphone-ipa-manager/)
Screenshots, feature guide, setup instructions, and Windows downloads.

A Windows desktop app for managing Apple accounts, installing assigned IPA files,
and renewing provisioning profiles. An optional private Discord bot exposes the
same saved assignments and job status.

## Download

The latest Windows release is **0.4.1**.

[**Download Windows installer — 0.4.1**](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.4.1-preview/iPhoneIPAManager-0.4.1-setup-x64.exe)

[Portable Windows x64 ZIP](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.4.1-preview/iPhoneIPAManager-0.4.1-windows-x64.zip)

[Release notes](https://github.com/hxzy69/iphone-ipa-manager/releases/tag/v0.4.1-preview)
· [Source code](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.4.1-preview/iPhoneIPAManager-0.4.1-source.zip)
· [Checksums](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.4.1-preview/SHA256SUMS.txt)

Run the setup EXE, or extract the portable ZIP and run `iPhoneIPAManager.exe`. Python is not required. The setup is per-user and does not request elevation.

[Installer checksum](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.4.1-preview/SETUP-SHA256SUMS.txt) · [Complete changelog since 0.2.0](CHANGELOG.md)

**Release status: 0.4.1 preview.** Verified with 580 automated tests and 28
packaged runtime checks. This release adds a searchable IPA Library, local update
suggestions, assignment history and undo, guided setup, live progress and guarded
run review. See [release validation](docs/VALIDATION.md) for verification details.

## Start here

1. Use Windows 11 x64. Install Apple's device software and confirm it recognizes
   your unlocked iPhone over USB. Approve **Trust This Computer** on the phone.
2. Extract the Windows release to a permanent folder and run
   `iPhoneIPAManager.exe`. Python is not required for the standalone release.
3. Choose a folder for your IPA files, then **Save settings**. Copy completed IPA
   files there using any transfer tool.
4. Click **Connections → Pair connected iPhone**, then **Add Apple account**. Sign in locally,
   complete Apple's verification, and choose an IPA to assign to that account.
5. Click **Check setup**, then **Install apps** for USB installation. The app works in
   the background; **Stop** requests cancellation.

Start with USB. Set up local Wi-Fi, remote access, Discord, and automatic renewal
after the first installation works. The app includes an offline **Setup guide**.

Desktop **Help & updates** and Discord `/updates` show the running version and
check official releases. See [updating the app](docs/UPDATING.md) for upgrading
without losing settings and for restarting background components safely.

## What it does

The 0.3.0 release combines a redesigned desktop, per-user installer, and all
reliability improvements developed since public 0.2.0. See the [complete
changelog](CHANGELOG.md), [installation guide](docs/INSTALLING.md), and
[dashboard/queue guide](docs/DASHBOARD_AND_QUEUE.md).

- Keeps an ordered list of up to 25 accounts, each assigned one IPA. Apple limits
  on apps, certificates, devices and App IDs still apply.
- Accepts version changes by choosing a new file; no executable rebuild is needed.
- Includes an [IPA Library](docs/IPA_LIBRARY.md)
  with local update suggestions and assignment undo, plus [guided setup,
  run review and live progress](docs/PRODUCT_WORKFLOWS.md). Each account keeps one IPA assignment.
- Validates IPA archives and snapshots assignments before launching jobs.
- Prefers an authenticated local-network route when available; otherwise uses an
  already-established remote tunnel.
- Supports profile-only renewal, with a full-install fallback for known profile
  rejection, expiry, or pending IPA changes. Ambiguous transport failures stop for
  inspection rather than blindly repeating an installation.
- Checks an opt-in six-day renewal schedule at Windows sign-in and every 15
  minutes. If the PC was off, an overdue run waits for a usable connection.
- Shares settings between the desktop UI, Discord, and scheduled work. Conflicting
  unsaved GUI edits require an explicit reload.
- Stores the Discord token with Windows user encryption and Apple credentials
  through the native backend's Windows Credential Manager integration.

## Optional features

| Feature | Setup |
| --- | --- |
| Local Wi-Fi installation | [Connection guide](docs/CONNECTIONS.md): Apple Wi-Fi sync and a separately installed fixed-purpose bridge service |
| Remote use through Tailscale | **Remote setup**, then establish a ready tunnel on home Wi-Fi before leaving |
| Private Discord commands | **Discord control** and [Discord guide](docs/DISCORD.md) |
| Automatic renewal | Install all assigned apps first, verify remote refresh works, then use **Automatic renewal** or `/autorefresh` |

The app never launches an administrator approval prompt. Installing Apple device
drivers or the optional Windows bridge service is a separate local administrator
task. Normal startup, account sign-in, installation and Discord control run with
ordinary user permissions. Scheduled work requires the owner to be signed in;
turning on the PC without signing in does not start the user-owned tasks.

## Discord commands

`/panel` opens the control panel; `/help` explains the commands.

| Command | Purpose |
| --- | --- |
| `/accounts`, `/files` | Browse account numbers, assignments and available IPAs |
| `/login`, `/login_cancel` | Add an account with follow-up verification prompts, or cancel sign-in |
| `/inbox`, `/assign` | Choose an account and IPA; `/inbox` also starts its installation |
| `/sideload` | Fully sign and install all accounts, or a selected account |
| `/refresh` | Renew all assigned apps; known invalid/expired profiles can trigger a full install |
| `/status`, `/history`, `/check` | Connection, progress, receipts and setup checks |
| `/settings`, `/autorefresh` | View or adjust supported preferences and scheduling |
| `/stop` | Request safe cancellation of an active job |
| `/dashboard`, `/queue` | View verified outcomes or queue work until connectivity is available |
| `/retry` | Queue full installs for unfinished accounts with unchanged assignments |
| `/backup` | Export assignments/preferences or confirm a restore |
| `/updates`, `/update` | Check releases, prepare a verified update, activate or roll back |
| `/diagnostics`, `/maintenance` | Export redacted health information, check disk space or clean old logs |

Account and file options offer autocomplete. Commands are restricted to one
configured owner, server and channel. Local sign-in avoids sending your Apple
password through Discord; Discord sign-in remains optional and discloses its
privacy implications before opening the form.

## Data and migration

Both source and EXE runs use `%LOCALAPPDATA%\iPhoneIPAManagerCommunity`. The source
tree is not a settings directory. `IPA_MANAGER_DATA_DIR` can select another
absolute path for development; keep it consistent across all launched processes.
Task registration preserves an explicit custom directory in the action arguments.

No existing personal installation is imported automatically. To migrate, stop its
jobs, back up its state, and review [migration](docs/MIGRATION.md). Never commit
live configuration, signing material, device pair records, or IPA files.

## Development and release builds

See [BUILDING.md](docs/BUILDING.md) for a clean Windows build, pinned dependencies,
tests, license collection and release archives. The Python package retains the
internal `iphone_detector` name to avoid breaking its worker protocol.
Source development requires Python 3.14; earlier Python versions are not
supported. The packaged Windows application includes its own runtime.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\tools\build.ps1
```

Source layout:

```text
iphone_detector/   GUI, Discord, account state, jobs, transport and renewal
native/            Headless Rust authentication/signing helper
vendor/isideload/  Modified upstream signing/transport library
tools/            Build, tests, dependency provenance and release checks
tests/            Device-free regression tests with explicit sample accounts
docs/             User, operator and contributor documentation
```

## Credits

This project derives its native signing integration from
[iloader](https://github.com/nab138/iloader), by nab138 and contributors.
[JAS](https://github.com/jkcoxson/jas), by jkcoxson and contributors, inspired the
profile-renewal and IP-device research; no JAS code was copied. It also relies on
isideload, idevice, netmuxd, pymobiledevice3, the pmd networking libraries, and
apple-codesign-quick. Their authors, source locations and license notices are
credited in [NOTICE.md](NOTICE.md) and the release's dependency inventories.


## Limits and support

VPN connectivity cannot make iOS expose a service that iOS has stopped listening
on. Remote-only cold start and uninterrupted roaming are not guaranteed. Mobile
transfer speed depends on the PC's upload, the mobile network, latency, and
whether Tailscale uses a relay. A profile readback is not proof that an app will
launch after the previous profile expires. See [known limits](docs/CONNECTIONS.md).

Read [SECURITY.md](SECURITY.md) before sharing logs or reporting a security issue.
See [data and privacy](docs/PRIVACY.md) and [troubleshooting](docs/SUPPORT.md) for
storage, external integrations, maintenance and recovery guidance.
Contributions are covered by [CONTRIBUTING.md](CONTRIBUTING.md). The public application is
GPL-3.0-or-later; third-party components retain their licenses in [NOTICE.md](NOTICE.md).

