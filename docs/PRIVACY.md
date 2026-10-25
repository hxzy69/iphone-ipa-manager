# Data and privacy

The application runs on the signed-in Windows PC. It has no built-in analytics,
advertising service or hosted account database. Operating Windows services and
third-party integrations can have their own data practices.

## Where information goes

| Information | Handling |
| --- | --- |
| Apple password | Local sign-in sends it through an anonymous pipe to the native helper; successful sign-in stores credentials in Windows Credential Manager |
| Optional Discord sign-in | Discord processes submitted passwords and verification codes before delivering them to the local bot; ephemeral forms are not end-to-end encrypted |
| Discord bot token | Stored locally using Windows user-bound DPAPI encryption |
| Phone pairing records | Kept by Apple/device-service software; these are sensitive device-access material |
| IPA files | Read from the selected folder, copied into a private job input directory, signed locally and transferred to the selected paired phone |
| Accounts, file paths and job history | Stored under the current user's application data directory |
| Configuration backup | Contains account emails, IPA names and selected preferences; excludes credentials, pairing and absolute IPA paths |
| Support report | Uses an allowlist of health fields; excludes credentials, account identities, device IDs, paths and raw logs |
| Update check/download | Contacts the official GitHub repository; does not send application configuration or Apple credentials |
| Dependency advisory check | A developer/build-time operation; submits public package names and versions to OSV, not user data |

Authentication contacts Apple and the configured anisette service. Remote access
uses separately configured Tailscale connectivity. Discord commands and replies
are processed by Discord. Review those providers' policies before enabling the
integrations. The application is not affiliated with these providers.

## Retention and removal

Private state is under `%LOCALAPPDATA%\iPhoneIPAManagerCommunity`, unless an
explicit alternate data directory was selected. Closing the desktop window does
not necessarily stop the separately configured Discord bot or remote daemon.
Disable those startup tasks and stop active work before moving or removing an
installation.

Maintenance cleanup is explicit. It removes eligible successful-run logs older
than 30 days, terminal queue/delivered-report records older than 90 days, and
obsolete download ZIPs older than seven days. It preserves unresolved jobs,
selected IPAs, completion receipts, installed executables, prepared updates and
rollback versions. Main application logs also use size-based rotation.

Existing credentials use the upstream `iloader` credential namespace for
compatibility. Deleting that shared material may affect an upstream installation.
Do not remove Apple pairing records or shared credentials as part of routine
cleanup. A complete account/device reset should be deliberate and separately
verified.

Protect the Windows account and backup exports. Never upload raw logs, pairing
records, credential files or screenshots containing secrets to a public issue.
Local encryption does not protect against a compromised signed-in Windows session.

This describes the current implementation. A future hosted or paid service needs
its own accurate service terms, privacy disclosures and support commitments.
