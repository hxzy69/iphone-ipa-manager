# Troubleshooting and support

Start with **About and updates → Save support report**, or Discord `/diagnostics`.
Record the application version, connection type, operation, approximate time and
whether any accounts completed. Reports are redacted; raw logs remain private.

| Symptom | Safe next step |
| --- | --- |
| Queue says waiting for connection | Check the paired phone and PC connectivity; use `/status`. A queued request has not yet installed anything |
| Queue is paused after a failure | Inspect `/history` and the phone. Dismiss the failed request only after review; `/retry` skips matching verified completions |
| Refresh cannot connect remotely | Try home Wi-Fi with Tailscale connected. A VPN cannot force iOS to reopen a stopped installation service |
| New IPA is not installed | Check `/accounts` and `/files`; choose the account and file again. Changed assignments require a full install |
| File changed after selection | Wait for the transfer to finish, choose it again and start a fresh request. Existing queued snapshots are intentionally not rewritten |
| Archive rejected | Use a completed, structurally valid IPA. Encrypted archives, unsafe links/paths and excessive expansion are rejected before signing |
| Apple requires verification | Use the existing local or optional Discord sign-in dialogue. Do not post passwords or codes in channel messages or issues |
| Settings cannot be read | The desktop can preserve unreadable settings in a backup and reopen setup. Existing credentials and pairing are kept. Restore a configuration backup deliberately |
| Update activation failed | Keep the old executable. Check updater status and startup settings; do not delete the previous installation until the replacement is verified |
| Automatic renewal needs attention | Inspect the final job receipt and phone first. A successful verified renewal or installation can resume scheduling; ambiguous work is not replayed automatically |
| Queue says assignment or file changed | Select the current IPA and account again. Dismiss the old failed request after review, then queue a new request; queued selections are not silently rewritten |
| Queue says worker could not start | This request did not launch a worker. Check disk space, folder access and local queue log, then review/dismiss the failed request before creating a new one |
| Queue says process tracking unavailable or completion unverified | A worker may have started or completed partially. Inspect the phone and history before retrying; the program pauses rather than repeating uncertain work |
| Background service stopped | Diagnostics show the last service exit separately from live connection state. Inspect the component log and preserved settings. Repair the cause before restarting; a prior failure record is not proof that the service is currently offline |

The PC must be on, awake, connected and signed in for user-owned startup tasks.
When it was off at the due time, renewal waits for connectivity after sign-in.
Expired or missing apps can require a full install. The last verified receipt is
evidence of the recorded operation, not a promise that external services or the
phone will remain available.

For a bug report, provide the redacted support report and a minimal reproduction.
State the Windows and iOS versions if known. Keep account identities, device IDs,
network addresses, full paths, tokens, pairing files and raw logs out of public
reports. Read the repository's `SECURITY.md` for private security reporting.
