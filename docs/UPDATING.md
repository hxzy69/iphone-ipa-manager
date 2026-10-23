# Updating the Windows app

## Guided updates in 0.2.0 and later

The candidate adds **Download and verify update** and **Activate prepared update**
to About and updates. Discord `/update` offers download, activation and rollback
actions, with an explicit restart confirmation. `/updates` remains a read-only check.
Existing 0.1.2 installations need one manual upgrade before these controls exist.

Download checks the official repository, retrieves checksums and matching
Windows/source ZIPs, verifies hashes and the executable receipt, bounds archive
expansion, rejects unsafe Windows paths, and extracts into a unique local version
folder. It does not stop running jobs. The publishing account remains the trust
root; these checksums are not a separate signing authority.

Activation requires idle installations and no pending queue. It runs the prepared
EXE's self-test locally before stopping the old components. Then it closes the old
GUI, stops the owned bot/tunnel, migrates existing ordinary-user task actions,
and starts the new build. Existing task arguments, triggers (including renewal
repetition), settings and permissions are preserved. The application data
directory stays the same. No administrator prompt or in-place EXE overwrite is used.

The previous executable remains on disk and its hash is recorded. Use the rollback
button or `/update` rollback to activate it again. Startup migration failures
attempt to retarget tasks back and relaunch the previous version. If Windows
prevents that repair, the updater marks the state for local review: open the
previous release and re-enable its startup controls. Future configuration-format
changes may require a private settings backup as well as the previous executable.

A stopped tunnel may require home Wi-Fi to reconnect. Prefer updating at home.
Real reboot, live startup migration and roaming acceptance remain open; the
candidate's failure handling is covered by isolated process/COM tests.

## Manual updates and release checks

Open **About and updates → Check for updates**, or use `/updates` in your private
Discord channel. The installed version appears in the desktop title and `/status`.
GitHub is contacted only when you request a check. Checks use the public API,
send no credentials, configuration, logs or device identifiers, and cache successful
responses for five minutes. GitHub still receives the normal network request and
its source IP address. Offline failures leave all running work alone.

The checker accepts release links only from this project's GitHub repository and
compares version numbers numerically. Drafts and GitHub prereleases are excluded.
The public preview release currently uses the normal latest-release channel;
check its release notes and validation limits before upgrading.

## Install a downloaded release

1. Let installations and refreshes finish. Stop USB watching if enabled.
2. In **Discord setup**, disable automatic renewal, turn off **Keep the Tailscale
   tunnel open**, and click **Stop Discord bot**. Wait for the tunnel to stop,
   then close the desktop app. Restarting the tunnel may require the iPhone on
   home Wi-Fi; update there when remote continuity matters.
3. Download the Windows ZIP and checksums from the release page. Verify the ZIP's
   SHA-256 against `SHA256SUMS.txt`, then extract into a new permanent folder.
   Keep the previous folder until the new build works.
4. Launch the new EXE. Settings remain in the community application data folder;
   do not copy a bundled example configuration over your saved settings. If using
   a custom data directory, keep the same argument/environment setting.
5. Open **Discord setup** in the new app. Turn Windows sign-in startup off and
   back on, then re-enable the tunnel and automatic renewal if you used them.
   These controls register tasks against the new EXE. Start the bot and use
   `/status` to confirm its version and connection.

The app does not replace executables, change startup tasks, or shut down workers
as part of a release check. For an unsuccessful upgrade, stop the new processes
and repeat startup registration from the previous folder. Keep a private backup
of your application data before changing versions; older builds may not understand
future configuration formats. Never post that backup to GitHub or Discord.

For builders, `python tools/verify_release.py <release-folder>` verifies the
downloaded source/Windows archives, checksums, embedded matching source, and
executable self-test receipt without running the downloaded executable. Checksums
detect damage; they are not a substitute for trusting the publishing account.

## Support snapshots

**About and updates → Save support report** saves a small JSON file. Discord
`/diagnostics` returns the same health fields privately. Reports include the
running version, runtime, configured account count, device-selection flag, remote
tunnel state, active job count and renewal flags. Unreadable sections are marked
without copying exception messages. They exclude Apple accounts, filenames,
device identifiers, network addresses, paths, tokens and raw logs. Reports are
created only on request; the app does not upload desktop reports automatically.
