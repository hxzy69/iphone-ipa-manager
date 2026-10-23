# iPhone IPA Manager

A Windows desktop app for managing Apple accounts, installing assigned IPA files,
and renewing provisioning profiles. An optional private Discord bot exposes the
same saved assignments and job status.

## Download

[**Download for Windows 11 (x64) — 0.1.2 preview**](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.1.2-preview/iPhoneIPAManager-0.1.2-windows-x64.zip)

[Release notes](https://github.com/hxzy69/iphone-ipa-manager/releases/tag/v0.1.2-preview)
· [Source code](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.1.2-preview/iPhoneIPAManager-0.1.2-source.zip)
· [Checksums](https://github.com/hxzy69/iphone-ipa-manager/releases/download/v0.1.2-preview/SHA256SUMS.txt)

Extract the ZIP and run `iPhoneIPAManager.exe`. Python is not required.

**Release status: 0.1.2 preview.** The existing installation engine has device
testing behind it. This public distribution introduces clean onboarding and a
smaller native helper; its validation and remaining device checks are recorded
in [release validation](docs/VALIDATION.md). Experimental remote renewal is not
a guarantee that an app will remain usable indefinitely.

## Start here

1. Use Windows 11 x64. Install Apple's device software and confirm it recognizes
   your unlocked iPhone over USB. Approve **Trust This Computer** on the phone.
2. Extract the Windows release to a permanent folder and run
   `iPhoneIPAManager.exe`. Python is not required for the standalone release.
3. Choose a folder for your IPA files, then **Save settings**. Copy completed IPA
   files there using any transfer tool.
4. Click **Select connected iPhone**, then **Add Apple account**. Sign in locally,
   complete Apple's verification, and choose an IPA to assign to that account.
5. Click **Check setup**, then **Run now** for USB installation. The app works in
   the background; **Stop** requests cancellation.

Start with USB. Set up local Wi-Fi, remote access, Discord, and automatic renewal
after the first installation works. The app includes an offline **Setup guide**.

Desktop **About and updates** and Discord `/updates` show the running version and
check official releases. See [updating the app](docs/UPDATING.md) for upgrading
without losing settings and for restarting background components safely.

## What it does

- Keeps an ordered list of up to 25 accounts, each assigned one IPA. Apple limits
  on apps, certificates, devices and App IDs still apply.
- Accepts version changes by choosing a new file; no executable rebuild is needed.
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
| Private Discord commands | **Discord setup** and [Discord guide](docs/DISCORD.md) |
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
Contributions are covered by [CONTRIBUTING.md](CONTRIBUTING.md). The public application is
GPL-3.0-or-later; third-party components retain their licenses in [NOTICE.md](NOTICE.md).

