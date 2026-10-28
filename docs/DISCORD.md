# Private Discord control

The bot runs on your own signed-in Windows PC. Discord is a remote control; it is
not the installer or a cloud storage service for your IPA files.

## Library and workflow commands

These commands are also available as buttons in `/panel`:

| Command | Panel button | Purpose |
| --- | --- | --- |
| `/ipa_updates` | IPA updates | Find newer versions in the saved IPA folder; choose an account with autocomplete to review a replacement. |
| `/assignments` | Assignments | View recent assignment changes. Use `action:undo` and entry autocomplete to restore a previous IPA. |
| `/progress` | Progress | Show active account stages, elapsed time and recovery guidance. |
| `/setup` | Guided setup | Show configuration steps and current saved-state checks. |

The panel opens each command's default view. Account and history-entry selections
remain available through slash-command autocomplete. These controls use the same
owner, server and channel restrictions as the rest of the bot.

## Configure the bot

1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create/reset its token, and paste it only into this app's local
   **Discord setup** window. Leave the token field blank later to keep it.
3. Enable Discord **Developer Mode** under Advanced settings. Copy your user ID,
   private server ID and command channel ID into the three local fields.
4. Generate an OAuth2 invite using `bot` and `applications.commands` scopes. Give
   the bot **View Channel** and **Send Messages** in that private channel. No
   Administrator permission or Message Content intent is required.
5. Invite it, save settings, then **Start Discord bot**. Try `/status`, `/accounts`
   and `/panel`. A command from the wrong user, server, channel or a DM is denied.
6. Enable Windows sign-in startup only after confirming it works. Keep the release
   folder in a permanent location so its task action remains valid.

The bot itself does not require the optional legacy Wi-Fi service. You can start
it and configure accounts before device connectivity is ready. A device operation
still requires a supported authenticated route; use the queue to wait for one.

`/inbox` offers account autocomplete followed by filename autocomplete. The file
must already be in your managed IPA folder. After successful assignment, that
account's subsequent installs and renewals use the new file. `/assign` provides a
picker flow; `/files` lists available files. Any file-transfer tool may save IPAs
into this folder; the app has no dependency on LocalSend.

Prefer the desktop **Add Apple account** dialogue for local credential entry.
Optional `/login` uses private Discord forms and follow-up code/SMS prompts.
Ephemeral replies are still processed by Discord and are not end-to-end encrypted.
Passwords and codes are never slash-command options, process arguments, or
application log messages. Native sign-in saves credentials only after Apple
authentication and developer access succeed. Use `/login_cancel` to cancel.

The bot cannot operate while the PC is off, asleep, or signed out. Startup tasks
retry failed connectivity. In the local candidate, `/refresh` can use paired USB,
an authenticated local network session or the established remote transport;
`/sideload` performs a full installation. `/stop` requests safe cancellation, so a
device operation can take time to finish. Successful receipts, not an initial
“Started” reply, determine completion.

`/status` includes the version of the running bot. `/updates` checks the official
GitHub release and returns its download page privately. It uses the same release
checker as desktop **About and updates**, sends no credentials or configuration,
and caches successful checks for five minutes. Checks never install software or
restart a job. See [updating](UPDATING.md) before replacing a running installation.

`/diagnostics` returns a private support snapshot with version, account count and
health flags. It excludes account identities, filenames, device IDs, network
addresses, paths, credentials and raw logs. The desktop can save the same report
from **About and updates → Save support report**.

Version 0.2.0 adds `/dashboard`, `/queue`, `/retry`, `/backup`, and `/update`.
Queue actions and account/request selection provide choices or autocomplete;
retry offers recent-run autocomplete. Backup restores and update activation use
private confirmation buttons. Configuration exports contain account emails and
IPA names but no credentials or pairing; **Attach Files** permission is needed
only for Discord backup downloads. See [dashboard and queue](DASHBOARD_AND_QUEUE.md)
and [guided updates](UPDATING.md) for the recovery and restart semantics.

The local 0.2.1 build adds `/maintenance`: show free disk space or confirm cleanup
of old successful-run logs, terminal queue records, delivered reports and obsolete
download archives. It preserves selected IPAs, credentials, unresolved jobs,
completion evidence, the prepared update and rollback executables.

