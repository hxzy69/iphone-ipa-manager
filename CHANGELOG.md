# Changelog

## 0.4.1 — complete Discord panel — 2026-10-28

- Add IPA updates, Assignments, Progress and Guided setup buttons to `/panel`.
- Document all four commands and their panel actions in the Discord guide.
- Verify each new panel button dispatches the matching command and retains owner-only access.

## 0.4.0 — app library and guided workflows — 2026-10-28

- Suggest newer numeric IPA versions from the managed folder when the bundle ID
  matches and the newest file is unambiguous. Refresh the open library every
  15 seconds without automatically downloading, assigning or installing apps.
- Save the last 100 IPA assignment changes atomically with settings, serialized
  across processes. Restore a previous assignment through the normal validated
  path, refusing stale accounts, changed files and missing originals.
- Add a guided desktop setup covering the IPA folder, USB pairing, Apple sign-in,
  readiness checks and optional remote/Discord/renewal setup.
- Add live account stages, elapsed time and actionable failure guidance, without
  guessing completion times or treating preparation-only results as installs.
- Replace immediate manual install/refresh starts with a review of account, app,
  version and expected operation. Recheck reviewed files, phone and account scope
  before device work. Discord confirmations expire after three minutes.
- Apply run review to desktop and Discord queue submissions. Existing unattended
  automatic renewals continue without an interactive confirmation.
- Add private Discord `/ipa_updates`, `/assignments`, `/progress` and `/setup`
  commands, with account/history autocomplete and single-use replacement controls.
- Add a searchable desktop IPA Library with app names, versions, bundle IDs,
  file sizes and account assignments read from each IPA's main app manifest.
- Assign an IPA to an existing account directly from the library, using the
  existing shared job lock, archive validation, account identity checks and
  pending-install tracking used by Discord.
- Keep library scans and assignment validation off the GUI thread. Show
  unreadable or changing files without hiding the rest of the library.
- Show IPA app metadata in Discord `/files`, with bounded, paginated replies.
- Add an inert library preview and regression tests for metadata, caching,
  interrupted transfers, asynchronous controls and assignment handoff.
- Normalize malformed XML/date manifest errors across IPA browsing and importing
  instead of exposing exceptions from the property-list parser.

## 0.3.6 — responsive preparation and reliable recovery — 2026-10-28

- Keep queued-request controls available while IPA content is checked, and defer
  full hashing until the phone is reachable instead of repeating it while offline.
- Serialize renewal-setting saves across controllers to prevent overlapping
  Windows task and preference changes.
- Keep the userspace tunnel event loop responsive during IPA hashing; cancel
  preparation and join its thread before finalizing a cancelled remote request.
- Monitor worker cancellation before selection validation and propagate stop
  requests through controller, routing and backend input hashing.
- Preserve lightweight offline assignment checks in the queue and stop/join
  packaged test children on recovery-check timeouts.
- Cancel IPA imports during archive validation and remove their partial copies.
- Reject archive paths containing redundant separators or dot components before
  importing an IPA or extracting a downloaded release.
- Release Discord and tunnel singleton locks when startup validation fails, so
  corrected settings can be retried in the same process.
- Report Windows scheduler failures while saving renewal settings without leaving
  the renewal dialog disabled or overwriting saved preferences.
- Close every discovered RemotePairing connection even when saving a listener
  fails or another connection cannot be closed.
- Start and acknowledge the update's tunnel before launching controllers that
  can also start it. Wait for replacement shutdown before rolling back, including
  exited children and forced termination; pause for repair if shutdown is uncertain.
- Require paired TLS for the read-only installation-service diagnostic as well
  as execution, and validate saved certificates before wrapping sockets.
- Add regression tests for cancellation, archive aliases, startup failures,
  connection cleanup and update handover/rollback.

## 0.3.5 — verified connection and workflow fixes — 2026-10-28

- Acknowledge Discord /status before reading renewal or worker state, move those
  reads off the gateway event loop, and report failures after acknowledgement.

- Keep authenticated remote tunnels open when job-result storage fails; retain
  durable dispatch claims so uncertain requests cannot replay.
- Apply saved Discord authorization and token changes to the running listener,
  reconnecting after active installations finish, including during offline retries.
- Bind Discord Install now confirmations to their account assignment and reject
  phone, account or IPA changes during connection preflight.
- Require a matching verified installation content identity before profile-only
  renewal, so overwritten IPAs with unchanged versions receive a full installation.
- Preserve unresolved Wi-Fi bridge cleanup attention during automatic recovery.
- Make IPA copying, hashing and CRC validation cancellable and check cancellation
  before starting the native backend.
- Keep the Activity window polling after synchronization errors and perform
  account-enrollment validation outside the desktop event loop.
- Own complete native extraction roots through signing/export/installation and
  remove them on errors. Use job-scoped temporary directories and allow
  maintenance to remove old abandoned native inputs without deleting receipts.
- Stop replacement processes before rolling back an update after a failed final
  activation-state write.
- Pin the saved paired device certificate on execution lockdown and service TLS
  channels over TCP and network usbmux. Reject plaintext network sessions and
  services, close rejected connections, and never reconnect an unpaired client
  after a failed network certificate check.
- Add regression coverage, including real TLS handshakes with matching and
  mismatched certificates for socket-first and existing-stream connections.

## 0.3.4 — reliability fixes — 2026-10-27

- Preserve verified completion receipts and pause the queue for review when Wi-Fi bridge cleanup cannot be verified.
- Keep the desktop status loop running after transient failures, and retain remote setup results while work is in progress.
- Run queue discovery outside its storage lock, reduce idle probing, and prevent duplicate runner launches and misleading status updates.
- Improve Windows service and scheduled-task errors, limit restart attempts, and correct startup toggle recovery.
- Save Discord settings atomically, report missing credentials and update or backup failures, and handle notification results without a receipt.
- Remove temporary uninstaller copies after they exit, correct account scope and document update-signing limitations and Python requirements.
- Add regression coverage for recovery, queue concurrency and user interface error handling.

## 0.3.3 — connection and recovery reliability — 2026-10-27

- Refresh can use the authenticated Wi-Fi bridge when direct local renewal and
  local RemotePairing fail before device work starts. A failed or uncertain
  operation is not automatically replayed, and bridge cleanup is verified.
- Prevent stale desktop account selections and unsaved IPA edits from applying
  to a different account after external changes. Queueing checks the selected
  account identity before accepting a request.
- Reconcile account completion history after an interrupted index update and
  continue processing valid native events when progress logs contain damaged
  lines. Reject invalid numeric timestamps and process identities safely.
- Handle malformed Tailscale peer and network-adapter data, report USB routes
  correctly in Discord, and keep a job's installed-app inventory current.
- Harden the vendored block-buffer 0.10.4 cursor checks and stop the isolated
  native worker on panic. Include regression tests and third-party notices.
- Document the upstream advisory's corrected affected range. The OSV audit
  retains a version-specific, expiring exception for its stale range; the
  existing RSA advisory exception is also included in the dependency report.
- Add regression coverage for refresh routing, recovery, account changes and
  the patched dependency. USB refresh/install, Wi-Fi bridge refresh/install,
  and cellular tunnel refresh were exercised on a paired iPhone.

## 0.3.2 — recovery and notification fixes — 2026-10-27

- Report separate renewal-state corruption incidents independently while suppressing
  repeated alerts for the same unresolved incident. A successful disabled check
  clears the previous incident so later failures can be reported.
- Preserve private report ownership when Discord settings change: a new audience
  can receive its own report for the same event without receiving the old one.
  Corrupt existing report records no longer count as successfully queued.
- Recover valid native completion events even when another event log line contains
  invalid UTF-8, rather than aborting collection of the entire log.
- Harden Tailscale peer selection and local-network enumeration against malformed
  status replies, and refresh the installed-app inventory after each verified
  install so later accounts in a job upgrade instead of reinstalling.
- Validate shared saved numbers through one finite-number helper so queue, worker
  registry, outbox, renewal, and receipt checks reject NaN and overflowing values
  uniformly, and reconcile a receipt index that a crash left behind after its job
  receipt was durable.
- Re-check an account chosen in a dialog before acting: dashboard queue and retry,
  GUI import, and Discord flows verify the account is unchanged and name a
  still-existing run before dispatching.
- Vendor patched block-buffer 0.10.4 with checked cursor assertions and regression
  tests, and build the native worker with panic = abort so a panicking worker
  stops instead of resuming possibly inconsistent state. See the updated
  advisory assessment in docs/BLOCK_BUFFER_ASSESSMENT.md.
- Add regression coverage for these failure and recovery paths.

## 0.3.1 — live status recovery — 2026-10-25

- The desktop reads a fresh tunnel/renewal summary directly from the running
  service when available, recovering a stale display without restarting the tunnel.
- A Windows named pipe restricted to the current user and SYSTEM carries bounded,
  read-only JSON status. The client verifies the process executable and profile;
  stale heartbeats, unexpected peers, and malformed replies are rejected.
- Display recovery reports disagreements with local status files. It never copies
  credentials, changes renewal timestamps, or authorizes an installation.
- Starting the bot or tunnel prefers an existing, enabled, matching Windows logon
  task. Different profiles, executables, owners, and elevated tasks are rejected;
  running tasks are not launched again and disabled tasks are not enabled.
- A fresh live tunnel prevents duplicate startup attempts caused by stale files.
  File-based status remains available when the live channel cannot be reached.

## 0.3.0 — desktop redesign and consolidated reliability release — 2026-10-25

This is the next public release after **0.2.0**. It includes every development
milestone from 0.2.1 through 0.2.10, detailed below, plus the complete desktop
redesign. Those intermediate versions were not separate public downloads.

### Added

- A per-user Windows installer with payload verification, Start menu integration,
  repair, and an uninstall entry. Installation and removal preserve private app
  data and IPA files and do not request administrator elevation.
- Four desktop workspaces: Overview, Accounts & apps, Connections, and Settings.
  Overview shows remote-access readiness, renewal scheduling, and setup guidance.
- Consistent Apple sign-in/verification, Discord, remote connection, renewal,
  IPA import, device selection, checklist, updates, and installer windows.
- An explicit automatic-renewal settings window with a Save action; opening it
  no longer toggles the schedule. Controls lock while settings load or save.
- Discord setup tabs for bot connection, startup/renewal, and step-by-step setup.
  The desktop opens this as a child window rather than a separate setup process.
- Activity management tabs for accounts, queued requests, and history/recovery.
- A source-only safe GUI preview with a screen menu, sample accounts, sample
  verification, and replayable simulated installer progress. Preview actions do
  not sign in, install, change settings, or start background services.
- Dependency advisory checks, a CycloneDX dependency inventory, disposable
  installer/recovery/isolation checks, and more packaged runtime self-tests.

### Improved

- On small displays with large text, Overview can scroll and bottom controls remain
  visible. Normal desktop sizes retain the approved layout without an Overview scrollbar.

- Persistent desktop save/install/stop controls, clearer install-versus-refresh
  explanations, saved/unsaved/conflicting-edit feedback, and live shared settings.
- Keyboard traversal and focus visibility in long forms; safe Escape handling;
  scrolling for small wheel deltas; automatically hidden unused scrollbars.
- Text-aware window sizing, font-aware table rows, wrapping of long account
  emails, bounded device-name summaries, and narrow-window action reflow.
- IPA import uses an account picker and explains that importing assigns a file
  without installing it. Imports validate files and selections transactionally.
- Installer and uninstaller have compact layouts and show progress only while
  working. Real progress remains indeterminate where no reliable percentage exists.
- Updates explain check, download/verify, and activation stages separately.
  Setup/help documentation matches the new interface and retains upstream credits.
- Automatic routing prefers a trusted matching USB phone, then authenticated
  local connectivity, before an established remote tunnel. Direct LAN
  RemotePairing can replace unavailable legacy local installation services.
- Tunnel listener discovery and warm roaming recovery; background renewal,
  queue, Discord, and GUI receipt consistency; task migration including hidden
  renewal tasks; update rollback and cleanup. Detailed changes follow below.

### Fixed

- Installer ownership checks accept equivalent Windows short and long paths while
  retaining traversal and redirected-path protection.

- Upgrade `crypto-bigint` 0.7.5 → 0.7.6 and `ctutils` 0.4.4 → 0.4.5 to address RUSTSEC-2026-0387, then rebuild and retest the native helper.

- Remote attempts could be claimed again after interruption; dispatched attempts
  now pause for review rather than automatically repeating device work.
- Temporary-file cleanup and optional metadata errors could obscure final results.
- Malformed or stale completion records could incorrectly advance renewal or
  queue recovery. Completion is now bound to the current phone, account, and IPA.
- Concurrent notification producers could reset delivered acknowledgements.
- Lost/reconnecting tunnels could leave submission waiting indefinitely.
- Desktop imports could race account/folder changes or leave invalid assignments
  after cancellation; stale Discord account choices are also rejected.
- Corrupt settings, legacy preferences, caches, progress, and update startup
  acknowledgements now fail conservatively without silently resetting data.
- UI status could retain stale readiness after a failed read. Pending dialog
  timers now stop on close; larger-text uninstall layouts no longer oscillate.
- Long device names could displace pairing controls; larger text could hide the
  activity pane; keyboard focus could land outside the visible form.

### Removed / replaced

- The single long desktop form, unnecessary scrollbars, separate Discord setup
  process from the desktop, and the renewal button's immediate toggle behavior.
- The always-visible idle installer progress bar and cramped account radio lists
  in the importer. Existing native Windows file pickers remain native.
- Unsafe automatic replay after ambiguous dispatch, false successful recovery,
  and destructive overwrite paths for failed/cancelled imports and updates.
- No existing Discord commands or account sign-in/verification capabilities were
  intentionally removed. No credentials or pairing records need to be reset.

### Upgrade and limits

- Use the setup EXE or portable ZIP. Existing community settings are kept.
  Finish active jobs and keep the phone on home Wi-Fi while restarting tunnels.
- The software remains a community preview. Executables are unsigned. Fresh
  cellular-only tunnel acquisition, indefinite roaming, and app validity after
  expiry are not guaranteed. Profile renewal remains experimental.
- The dependency audit records a time-limited RSA exception; see the bundled
  audit and security documentation. Clean-machine, screen-reader, physical
  multi-monitor DPI, and exact-release long-term/device acceptance remain open.
- Historical device results are identified by their tested version; they are not
  presented as new device tests of this release. See docs/VALIDATION.md.

### Detailed included development history

The following 0.2.1–0.2.10 entries record the complete intermediate change history.
Statements about local-only delivery describe those milestones at that time;
all of their source changes are included in this public 0.3.0 release.

## 0.2.10 - consolidated reliability audit (local candidate, unreleased)

- Claim remote attempts durably before signing or device access. Interrupted and legacy dispatched requests pause without replay; preserve final receipts before best-effort signed-archive cleanup.
- Bound remote submission waits across offline/reconnecting states and total job time. Reject cancellation before publishing a request. Enforce typed final completion on every local/remote return path.
- Revalidate frozen account/file selections at remote dispatch. Reject stale Discord account selections after configuration changes, including the Install now follow-up. Preserve honest saved status after a confirmation delivery failure.
- Prefer paired USB for automatic installations, including Discord without the optional legacy service. Ignore malformed optional LAN caches and progress records.
- Preserve failed/cancelled USB-watch receipts. Keep ambiguous native dispatch paused even when progress events were never written.
- Publish operational reports atomically without resetting delivery acknowledgements under concurrent producers. Clean up replacement processes after malformed update startup acknowledgements.
- Share server-preference validation with account enrollment without changing the login dialogue. Reject non-object Discord configuration and unsafe release archive members with clear validation errors.
- Extend the packaged self-test with interrupted-dispatch and typed-completion checks. Add cross-component fault and concurrency regressions; retain external device/commercial acceptance limits.


## 0.2.9 - saved receipt validation (local candidate, unreleased)

- Reject boolean/float account numbers, duplicate slots and malformed content identities in recovered completion records. Invalid records cannot postpone renewal or report queued work as completed; no automatic replay is introduced.
- Use IPA Manager's configured anisette server independently of legacy iloader preferences. Unreadable or non-object legacy preferences return repair instructions and remain untouched; BOM-encoded JSON is accepted.


## 0.2.8 — transactional desktop imports (local candidate, unreleased)

- Copy, validate and save a desktop IPA import under the shared installation/settings lock. Recheck the selected account, folder and source identity before copying; preserve unrelated settings and persist the chosen receive folder.
- Cancellation before assignment removes only the new unique copy. Closing the dialog after a successful save preserves the committed IPA instead of leaving its assignment pointed at a deleted file.
- Refuse an existing import destination even if a generated filename collides, preserving the previous IPA and removing the uncommitted temporary copy.
- Normalize saved desktop field values and their live-sync baseline together, avoiding false unsaved edits and folder mismatches after path normalization.
- Distinguish missing settings from access errors by reading directly; an inaccessible profile cannot silently look like a fresh setup because an existence probe suppressed its error.

## 0.2.7 — settings and assignment consistency (local candidate, unreleased)

- Validate supported preference types before GUI construction, device work or saving. Preserve invalid originals and allow the existing local repair flow to quarantine malformed supported fields, not just broken JSON.
- Reject non-text account fields instead of converting null or numbers into filenames. Preserve unknown preferences and permit inaccessible folders to be corrected in settings.
- Clear pending IPA installations only when the phone, account email, source folder and content identity still match the completed request. Damaged or legacy snapshots retain pending status conservatively.
- Start the Discord listener independently of the optional legacy Wi-Fi service. Report Windows launch failures without changing saved settings.
- Remove only a failed update attempt's staging directory after download or verification errors; retain earlier verified update plans and their data.
- Preserve a final operation receipt before collecting optional account metadata, tolerate damaged progress events, and reject untyped/truthy native success results. Unreadable remote settings produce a final failure receipt before launch.

## 0.2.6 — failure reporting and recovery (local candidate, unreleased)

- Preserve clear queue failure reasons, show them in GUI and Discord, and persist a deduplicated Discord attention report. Distinguish failure to launch from failure to track an already-started worker; neither automatically repeats device work.
- Verify the queued selection against its completion snapshot before recovering a request as completed. Reject invalid process identities and non-finite timestamps when tracking workers and delivering operational reports.
- Continue cancelling other active workers when one cancellation marker cannot be saved; report only successfully persisted requests.
- Share conservative completion status between GUI and Discord history. Preparation, missing/invalid account snapshots and partial outcomes no longer appear completed; USB routes are displayed.
- Preserve completed refresh receipts when optional expiration metrics are malformed, while withholding the unverified expiration date.
- Contain unattended queue, renewal, Discord and tunnel exceptions with a nonzero exit, local incident status and a deduplicated safe report instead of a packaged crash dialog.

## 0.2.5 — release reliability (local candidate, unreleased)

- Include hidden renewal tasks when migrating an installation, disabling renewal, or uninstalling. Previously Windows omitted these tasks from ordinary enumeration, leaving actions pointed at an older executable.
- Recognize both supported data-directory argument forms when migrating tasks, while retaining owner, profile and permission checks.
- Treat a missing task as already disabled, including Task Scheduler's wrapped COM error form. Continue reporting access-denied and other unexpected failures.
- Allow Discord installations to use authenticated direct LAN RemotePairing without requiring the legacy Wi-Fi service or an already-open remote tunnel.
- Move Discord installation preflight off the gateway event loop. Prevent concurrent startup and honour cancellation before launching a worker, including when the interaction task is cancelled.
- Add a disposable Windows install/repair/removal acceptance tool and a commercial-release evidence check bound to the exact executable. These checks do not replace clean-machine testing or independent review.

## 0.2.4 — remote listener recovery (local, unreleased)

- Recover a changed remote listener when multicast discovery returns no results by querying the authenticated, on-link phone directly. Validate its pairing authentication tag and authenticate the returned endpoint before saving its port.
- Bound discovery requests and responses, reject unrelated replies, and reuse the same fallback during remote setup and persistent tunnel recovery. No administrator permission, new pairing or app installation is needed for discovery.
- Use a verified direct LAN RemotePairing tunnel when the older local installation service closes, for both renewal and installation. Keep the persistent Tailscale session available for roaming.
- Mark locally executed requests so the remote daemon cannot claim them a second time. Never replay a failure after submission on another connection.
- Retain a live roaming transport during temporary installation-service failures, stop claiming readiness while checks fail, and reconnect only after detecting transport closure.
- Verify direct LAN renewal, warm cellular roaming and automatic bot/tunnel startup after a real reboot. Fresh cellular-only tunnel acquisition remains refused by the phone.
- Migrate the private installation's hidden renewal task to the current EXE and synchronize the verified refresh receipt while preserving its completion time; scheduled checks require no elevation or additional app writes.

## 0.2.3 — desktop setup and recovery guidance (local, unreleased)

- Add a per-user setup EXE with payload verification, Start menu integration and a Windows uninstall entry; never request elevation.
- Keep versioned application copies, preserve private settings and IPAs on removal, reject unsafe manifests and restore installation integration after failed updates.
- Share an actionable setup/recovery checklist between the desktop and Discord `/check` without exposing private identities or claiming a live connection was tested.
- Scope update process discovery to the exact settings profile, account for observed early expiry in support diagnostics and fit the desktop minimum size to the screen.
- Fall back from a failed read-only local renewal preflight before device work begins; never replay a failure after submission. Scope independent tunnel daemons by profile while preserving shared installation exclusion.
- Prefer a matching trusted USB phone for refresh and show the USB route on the dashboard. Resolve normalized phone IDs to the pairing record's original cache identifier for remote discovery.
- Allow USB/local refresh even when the configured remote tunnel is offline. Balance native COM initialization without invalidating caller-owned proxies; preserve shortcut targets and settings-profile arguments when posting notifications.
- Keep the existing sign-in flow and existing private deployment intact. This build remains local.

## 0.2.2 — renewal and queue recovery (local, unreleased)

- Reconstruct legacy renewal timers from verified job selections; a changed phone or IPA assignment cannot inherit an unrelated success timer.
- Seed automatic renewal from matching desktop and background completions, preserving their actual completion time and ignoring future-dated results.
- Skip network probes when renewal is not due, another installation is busy, or an unresolved run needs attention.
- Defer temporary connectivity-check failures without claiming a job. Queued requests resume when connectivity returns, and clear their waiting status when started.
- Reject malformed saved device identities without crashing renewal or account-outcome verification.
- Preserve the existing login flow. This build is saved locally and has not replaced the installed application or been published.

## 0.2.1 — local hardening build (unreleased)

- Bind renewal and recovery to the current phone, accounts and IPA content; retain per-account outcomes beyond the recent history window.
- Hash IPA inputs and install from verified private copies. Validate bounded archive paths, metadata, executables, links, file counts and expansion before native work.
- Require HTTPS anisette settings consistently and update ZIP, TLS, certificate and runtime dependencies with advisory checks and a time-limited RSA exception.
- Wait for authenticated connectivity before queue dispatch and retry temporary queue-store contention without replaying uncertain installations.
- Support profile renewal over paired local Wi-Fi, bring renewal forward for observed early profile expiry, and remove the conflicting one-hour scheduler limit.
- Verify replacement-process startup during guided updates and roll back failed activation.
- Move desktop imports and dashboard actions off the UI thread, add cancellable imports, adapt initial window sizes and preserve unreadable settings during explicit local repair.
- Add conservative storage maintenance in the desktop and Discord, a dependency inventory/advisory gate, and an explicit draft-first release workflow.
- Preserve existing Discord sign-in behavior and upstream credential compatibility. No public release or installed-profile migration has been performed.

## 0.2.0 — workflow controls (preview)

- Add a shared desktop/Discord dashboard for account outcomes, observed profile expiry, renewal status, last route and measured upload speed.
- Persist account-level verified completion receipts and provide explicit retries that skip matching completed accounts. Recover the renewal schedule conservatively after partial retries.
- Add a durable FIFO network queue with assignment snapshots, account/request autocomplete, cancellation, sign-in startup and fail-closed interruption recovery.
- Add validated configuration export/restore in the GUI and Discord, excluding credentials, pairing and local paths; keep a local pre-restore assignment backup.
- Prepare verified updates in separate version folders; require local runtime checks and idle work before activation, preserve startup triggers, and retain the previous executable for rollback.
- Extend device-free acceptance coverage for fresh profiles, process startup, crash recovery, update failure, backup privacy and GUI controls. Physical device, real reboot and long-term expiry acceptance remain separate requirements.

## 0.1.2 — update visibility

- Show the running version in the desktop title, About window, Discord `/status`, and packaged self-test receipts.
- Add manual GitHub release checks in the desktop and private Discord `/updates`; checks run away from the UI thread, cache successes, and never restart active jobs.
- Validate public release versions and official links, bound responses and network timeouts, and present actionable offline/rate-limit errors.
- Export a support snapshot from the desktop or Discord `/diagnostics` using explicit health fields, excluding accounts, paths, credentials, identifiers and raw logs.
- Document upgrade, startup-task migration and rollback steps; add receipt and archive integrity verification to release packaging.

## 0.1.1 — authentication retry fix

- Retry transient Apple login request failures, connection resets, unexpected EOFs, and login timeouts on the current account.
- Limit login to three attempts with two- and four-second backoff; credential and verification failures still stop immediately.
- Keep signing and installation outside the retry boundary so completed accounts are not replayed.
- Run native authentication retry regression tests during release builds.

## 0.1.0 — public preview

- Empty first-run account state, local Apple sign-in and a guided setup checklist.
- Native-only background execution; retired screenshot and coordinate automation removed.
- Shared desktop/Discord assignments, dynamic accounts, file autocomplete and job history.
- Optional authenticated local routing, persistent remote transport and six-day renewal.
- Conservative refresh fallback and durable completion reporting.
- Separate community state/tasks, pinned build inputs, CI, source allowlists and license inventory.

