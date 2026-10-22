# Private Discord control

The bot runs on your own signed-in Windows PC. Discord is a remote control; it is
not the installer or a cloud storage service for your IPA files.

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
retry failed connectivity. `/refresh` requires the established remote transport;
`/sideload` performs a full installation. `/stop` requests safe cancellation, so a
device operation can take time to finish. Successful receipts, not an initial
“Started” reply, determine completion.

