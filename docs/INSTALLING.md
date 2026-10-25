# Install, repair and remove

Release 0.3.0 includes a Windows x64 setup EXE alongside the portable ZIP.
Code signing has not been added; Windows may show an unknown-publisher warning.

Run `iPhoneIPAManager-0.3.0-setup-x64.exe` and choose **Install / repair**. Setup
runs for the current Windows user without elevation. It verifies the embedded
distribution and copies it into a new version directory under
`%LOCALAPPDATA%\Programs\iPhoneIPAManagerCommunity`. The manager appears in the
Start menu and Windows **Installed apps**. Python is not required.

Close a previously installed manager and stop its background listeners before
repairing or removing that installation. Active device work blocks these actions.
Setup does not silently terminate a signing or installation job. Existing startup
tasks belonging to that installation are retargeted during repair. Different
installations and settings profiles are preserved.

The portable Windows ZIP remains supported: extract it into a permanent folder
and start `iPhoneIPAManager.exe`. Installing the community candidate does not import
the owner's private development configuration or replace its deployment.

## Setup checklist

Use **Setup checklist** in the desktop or `/check` in the private Discord channel.
Both show the same saved-configuration findings and next steps. They do not log
into Apple, install apps or prove live connectivity. **Check setup** additionally
validates the selected IPA archives before the first installation.

Refresh prefers the selected iPhone over USB when attached, verifies its identity
and installation service, then uses the same renewal/fallback engine. Without USB,
it tries paired local Wi-Fi and the configured remote tunnel.

Start with USB and verify the installed app opens. Add Wi-Fi, Discord and automatic
renewal afterward. Remote use requires a verified tunnel established while the
phone is reachable; a VPN does not remove iOS service restrictions.

## Repair and updates

Run setup again to copy a fresh version without replacing the previous executable.
Settings and credentials remain in the private data directory. For verified guided
updates, the manager stages a version within its installation, updates shortcuts
and startup actions, and restores the previous installation record if activation
fails. Keep the last working copy until the replacement is verified.

## Uninstall

Use Windows **Installed apps → iPhone IPA Manager → Uninstall**. The uninstaller
runs from a temporary copy, checks ownership, removes only recorded application
files and owned startup tasks, and preserves unknown files in the install folder.
It preserves private settings, credentials, pairing records and IPA files. A
privileged bridge service installed separately by an administrator is not removed.
The temporary uninstaller copy can remain in the Windows temporary directory.

Changing or corrupting `installation.json` blocks removal rather than guessing
which files belong to the manager. Restore the ownership record or repair locally.

## Build the setup EXE

After producing the verified Windows/source archives, run
`powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_installer.ps1`
from the source root. The build uses Windows' .NET Framework compiler and embeds
an `asInvoker` manifest. No signing credentials are assumed. Setup's checksum is
in `artifacts/SETUP-SHA256SUMS.txt`, separate from the two-archive updater manifest.
