# IPA Library

The desktop library shows the app name, version, bundle ID, file size and saved
account assignments for IPAs in your configured folder. It reads metadata from
the IPA, so the displayed app name can differ from its filename.

1. In **Accounts & apps**, choose your IPA folder and **Save settings**.
2. Copy completed IPA files into that folder using your preferred transfer tool.
3. Open **IPA Library**. Search by app name, version, bundle ID or filename.
4. Select a file and the account that should use it, then **Save assignment**.

Saving checks the complete archive and updates the same settings used by the
desktop, Discord and automatic renewal. It replaces only the selected account's
IPA assignment. Each account has exactly one saved IPA: choosing a different file
replaces the previous assignment rather than adding another one. It does not copy,
delete or install the file. The next install,
refresh or automatic renewal uses the new assignment; a changed IPA receives a
full installation through the existing pending-assignment workflow.

The library uses saved settings. Unsaved edits in the main window are preserved
by the existing synchronization rules; conflicting edits need to be reloaded.
If an account was replaced or the IPA changed while the library was open, saving
is rejected. Refresh the library and choose again. Assignments also wait until
any running installation or refresh has finished.

**Refresh** rereads the folder and metadata. Files marked **Unreadable** remain
visible but cannot be assigned here. Wait for an unfinished transfer to finish
or replace a damaged file, then refresh. A readable manifest is not proof that
the archive is valid or that an app is trustworthy: full archive checks run
when assigning and preparing jobs. The library does not execute app contents.

In Discord, `/files` shows app names and versions alongside filenames, sizes and
account numbers, ten files per page. Long labels are shortened to fit Discord's
message limit. Use `/inbox` or `/assign` to select the full filename and account;
`/settings` shows the configured folder.

The GUI preview uses sample apps and cannot save assignments or run jobs.

## Newer versions in the folder

While open, the library checks the saved folder every 15 seconds. Choose an
account to see an available update, then use **Use newer IPA** to replace that
account's single assignment. This saves the choice without installing it.

Suggestions require the same bundle ID and a strictly newer numeric version.
They do not compare filenames, guess prerelease ordering, or choose between
multiple files sharing the newest version. Different variants may still have
the same bundle ID: inspect the file before accepting a suggestion. Ambiguous
files and nonnumeric versions remain available for manual assignment.
The library does not download updates from the internet.

## Assignment history and undo

Open **Assignment history** or use Discord `/assignments`. The last 100 changes
made by this build are recorded with the saved settings. There is no invented
history for earlier builds. No Apple password or Discord token is recorded.

Select a change to see both complete filenames. Restoring checks that the account
still has the recorded replacement and that the previous IPA is available in the
current managed folder. A changed previous file is rejected when its original
signature was recorded. The full archive is validated before saving the undo.
The restored IPA becomes the account's sole assignment and is marked for a full
installation on the next install or renewal. Undo does not uninstall an app or
restore an app's data on the phone.

Discord `/ipa_updates` lists suggestions; select an account with autocomplete to
get its private replacement button. `/assignments action:undo` offers history-entry
autocomplete and uses the same guarded restore operation as the desktop.
