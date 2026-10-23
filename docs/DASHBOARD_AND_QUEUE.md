# Dashboard, recovery and queued work

The desktop **Dashboard and queue** window and Discord `/dashboard` share the
same saved assignments and verified account outcomes. The desktop refreshes every
three seconds. Discord pages contain up to five accounts.

## Reading account health

Each account shows its assigned IPA, last verified outcome, operation and observed
profile expiry. Missing source files are flagged in the desktop table. Old
receipts are matched against the configured phone, account, IPA path and file
identity; replacing an IPA does not inherit the old file's verified status.

Profile expiry is displayed only after successful profile readback. A full install
may show **Unknown** until such evidence exists. Unknown does not mean expired or
valid. Profile readback also does not establish that an app launches after its
previous profile expires; that still requires a device acceptance check.

Connection details distinguish the **last selected route** from current tunnel
readiness. Recent upload progress supplies measured average MiB/s; stale samples
are hidden. No bandwidth probe or mobile-data transfer starts just by opening
the dashboard. The connection explanation identifies an offline, connecting,
waiting or available tunnel without claiming that a VPN can force iOS readiness.

## Queueing requests

In the desktop, select all accounts or one account, then choose **Queue network
install**. **Queue refresh (all)** always covers all configured accounts. In
Discord, use `/queue` and select an action; account and request fields have
autocomplete. Existing `/sideload` and `/refresh` retain their immediate-run
behavior. Queued requests use the network worker and the existing route selector.

The queue stores a snapshot of the phone, account assignments and source-file
identity when requested. It dispatches one worker at a time after other
installations finish. Changed files or assignments fail the request instead of
installing a different app. The queue has a 50-request pending limit.

Cancellation applies only to requests that have not started. Use `/stop` or the
main desktop Stop button for an active worker. Failed or interrupted requests
pause later requests. Inspect the phone and run history, then **Dismiss failure
and resume queue** or `/queue action:Dismiss failed request and resume`.
Dismissal acknowledges the failure; it does not repeat the failed installation.

Queueing normally registers an ordinary-user Windows sign-in task. Disable this
with **Resume at Windows sign-in** or `/queue`'s startup action if preferred. The
PC must be on and signed in. If startup registration fails, the request is kept;
opening the desktop or starting the bot resumes queue supervision. A reboot never
silently replays a worker whose outcome is unknown. A verified final receipt can
be recovered; an ambiguous interrupted request remains paused.

## Retrying partial work

Select a recent run and choose **Retry unfinished accounts**, or select its run
with `/retry` autocomplete. This queues **full installations** for accounts
without a matching verified completion. It skips matching completed accounts.
Changed phone/account/file snapshots and runs still active are rejected. Inspect
the device first when an outcome is uncertain: no receipt can prove a completion
that was lost before it reached disk.

Verified full installs within an otherwise failed run can clear only their own
pending assignment flags. A later successful retry can resume the renewal schedule
once every current account has newer verified evidence. The combined schedule
uses the earliest account completion, preserving the six-day renewal margin.

## Backing up assignments

Use **Export configuration backup** or `/backup action:Export`. The JSON contains
account emails, assigned IPA names and selected preferences. It deliberately
excludes passwords, Discord tokens, pairing records, device identifiers, local
paths, startup tasks and renewal receipts. It is **not encrypted**: keep it private.
Discord exports require **Attach Files** permission; local export does not.

Restore validates the schema and previews the account count before confirmation.
It is blocked during a run or while queue requests are pending. It preserves local
pairing, credentials and the IPA folder, saves a preferences/assignments backup
under the local `backups` folder, and marks imported assignments as requiring an
installation. Copy the corresponding IPAs into the configured folder and ensure
the Apple accounts are signed in before using Check setup and running work.
