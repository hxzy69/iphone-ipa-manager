# Public preview validation

## 0.3.3 — 2026-10-27

The release workflow built and verified the Windows installer and portable
package. The source passed 464 Python tests, native Rust tests, optimized
block-buffer tests, and targeted Miri checks. The dependency audit reports two
time-limited accepted advisories; see `third_party/security-exceptions.json`
for their scope and expiry.

Before release, device tests covered USB and Wi-Fi installation, USB and
cellular profile refresh, and the Wi-Fi bridge fallback. The user confirmed
the refreshed apps opened. These tests exercised candidate builds and source;
the published executable has not had a separate physical-device or fresh
Windows reboot acceptance test.

## 0.3.1 — 2026-10-25

443 isolated tests passed, including owner-restricted live status IPC, malformed
and stale replies, process/profile identity checks, duplicate-start prevention,
and validated scheduled-task launches. A real Windows task-to-client test recovered
stale display values across different filesystem views. The packaged executable
passed 19 self-tests and all 62 bundled application modules matched source.
This update changes status presentation and service startup; it does not claim
new device installation, expiry, roaming or reboot acceptance. It does not merge
conflicting profile files or use display status to authorize device operations.

## 0.3.0 — 2026-10-25

The release incorporates the intermediate results below. The approved GUI passed
427 source tests and a 30-case dialog resize pass at simulated 100%, 150%, and
200% Tk text scaling. These are not physical multi-monitor or screen-reader
acceptance tests. Packaged runtime and artifact checks are repeated for the
release; historical device results retain their original version labels. No
new phone installation or profile refresh is claimed for 0.3.0.

## 0.2.10 consolidated reliability audit - 2026-10-23

417 isolated tests pass. Added regression coverage spans crash recovery, locked
temporary IPA cleanup, interrupted dispatch without progress, USB automatic
routing without a legacy service, stale account pickers, corrupt discovery caches,
typed completion, bounded lost-tunnel waits, concurrent report producers, shared
server validation, malformed update acknowledgements and unsafe archive members.
Packaged self-tests now include interrupted-dispatch recovery and typed remote
completion checks, in addition to delivered-report deduplication.

The review traces GUI/Discord/queue/scheduler entry points through request
validation, connection routing, native signing/transfer, result persistence and
recovery. Installer/update/rollback, settings/backups, profile refresh eligibility,
account authentication, cancellation, notifications and archive boundaries were
included. This is a broad code and regression audit, not proof of every possible
runtime state. No new phone installation/refresh or physical reboot is claimed;
historical device evidence remains labeled. Code signing, clean-machine/device,
longer roaming/sleep/loss, expiry and independent commercial review remain open.


## 0.2.9 saved receipt consistency - 2026-10-23

400 isolated tests pass. Regression cases verify that damaged saved completion
records cannot postpone renewal or complete a queued request without replay.
Account numbers and content metadata are typed, duplicate slots are rejected,
and content-free schedule checks still allow intentionally omitted digests.
Legacy preference corruption cannot block an explicitly configured anisette
server. Missing, inaccessible, invalid, non-object and BOM-encoded legacy inputs
are covered without altering originals.

No new phone refresh is claimed. Device signing/transfer code is unchanged;
this follow-up validates saved identities and constructs requests more safely.
Historical 0.2.7 device evidence remains historical. Signing, clean-machine,
independent review and device/time-dependent acceptance remain open.


## 0.2.8 desktop import consistency — 2026-10-23

390 isolated tests and Ruff lint/format pass. New tests exercise actual temporary
IPA archives/settings with cancellation immediately before and after commit,
changed account/folder rejection before copy, failed-save cleanup, source-folder
persistence and shared-lock exclusion. Saved widget values and live-sync baseline
are normalized together. Inaccessible settings are distinguished from missing
settings without trusting a potentially suppressed existence error.

This follow-up changes the GUI/import transaction, configuration read error
handling and version metadata. The signing helper and installation/renewal
implementation are unchanged from the exact 0.2.7 binary that passed the real
three-account refresh below. A new refresh is not performed merely to change the
GUI; the existing old-expiry acceptance window is preserved. The 0.2.7 device
result is historical component evidence, not a claimed exact-0.2.8 device pass.

`tools/smoke_isolation.py` adds a repeatable standalone check with an empty working
folder, fresh app data, a system-only PATH and poisoned ambient Python paths. It
does not claim to be a clean-machine test. Exact binary verification receipts,
lifecycle, recovery and update/rollback reports accompany the private candidate.
Commercial signing, clean-machine, independent review and time-dependent/device
acceptance requirements remain open.

## 0.2.7 settings and worker acceptance — 2026-10-23

381 isolated tests and Ruff lint/format pass. The exact standalone EXE passes
17 runtime checks and all 59 bundled application/entry modules match source.
Six packaged fault scenarios pass with no device submission: missing IPA,
mismatched completion selection, corrupt queue, corrupt renewal, malformed
supported preferences and unreadable remote settings.

The candidate ran in the owner's actual Windows session against the existing
private profile. All three apps were renewed through direct LAN RemotePairing
in 59.87 seconds: 161,198 profile bytes, three verified readbacks, no reinstalls.
The existing persistent tunnel remained ready. No owner app-launch confirmation
or post-expiry launch is claimed for this candidate.

The latest common launch-after-old-expiry window is 30 October 2026 from
19:21:48 UTC until 20:41:42 UTC, with no intervening full reinstall. Preserve
the exact UTC evidence; local offsets change when daylight saving ends.

Supported malformed settings can now enter the existing explicit local repair
flow before GUI construction, with original bytes retained. Pending IPA
acknowledgment verifies current phone/account/folder/content. Failed update
staging is removed without deleting a previously verified plan. Optional progress
and completion-metadata failures cannot erase the final operation receipt.

Signing, clean-machine and broader device/roaming/reboot/expiry acceptance remain
open. This is a private candidate, not a commercial certification.

## 0.2.6 private recovery hardening — 2026-10-23

370 isolated tests and Ruff lint/format pass. New fault tests cover mismatched
queued completion snapshots, pre-spawn failures, process-tracking failures after
spawn, non-finite process/report timestamps, partial cancellation storage failure,
malformed optional expiry metrics and shared conservative GUI/Discord history.
Background service boundary tests verify nonzero exits, deduplicated incident
notifications and safe reporting even when status/notification storage fails.

`tools/smoke_recovery.py` provides repeatable packaged tests using disposable fake
inputs: missing IPA, another phone's completion receipt, corrupt queue JSON and
corrupt renewal JSON. No phone or credentials are used. Receipt files in the local
delivery identify which binary passed these scenarios; source tests alone are
not accepted as packaged verification.

The private deployment remains on 0.2.4. No new real-device acceptance or paid
approval is claimed. Signing, clean-machine setup, device/roaming/expiry and
commercial dependency review requirements from the preceding candidate remain.

## 0.2.5 private commercial-release candidate — 2026-10-23

355 isolated tests and Ruff lint/format pass. The packaged candidate passes all
17 runtime checks; all 58 bundled application and entry modules match source.
The native backend is unchanged. The owner deployment continues running 0.2.4;
this candidate has not replaced its bot, tunnel or renewal task.

A disposable Windows lifecycle check used the real filesystem, current-user
registry, shortcut and Task Scheduler. Installation, repair with retained old
binary, hidden-task migration at normal permissions, installed runtime self-test
and removal passed. Private settings and an unrelated file survived; owned
binaries, shortcut, registry entry and hidden task were removed. This is a real
developer-machine lifecycle test, not a clean-machine acceptance result.

The dependency audit queried 518 locked packages and found no unaccepted
advisories. The existing RSA exception remains, expires 23 November 2026 and
still requires independent commercial review. The final executable and installer
are unsigned; no usable code-signing identity is available in this user's store.
Windows Sandbox is unavailable. Clean-machine onboarding, exact-candidate device
acceptance, sustained roaming/recovery, reboot with overdue renewal, accessibility
and launch-after-previous-expiry remain uncompleted. Prior 0.2.4 device evidence
is historical evidence, not acceptance of this new candidate.

See COMMERCIAL_RELEASE.md for the repeatable lifecycle tool and release evidence
checker. Nothing was published or represented as a completed certification.

## Real reboot acceptance and renewal migration — 2026-10-23

After the owner rebooted without manually opening the application, the configured
Windows startup session reported both the Discord bot and remote daemon running
the expected 0.2.4 EXE at normal user permissions. The tunnel heartbeat was under
one second old and its PID matched the process started after boot. The owner
confirmed Discord `/status` showed 0.2.4 and Remote tunnel ready. Original phone
and three account assignments matched the verification reference.

Earlier startup failure reports were false alarms: the tool session read an older
profile-file view than the Windows startup session. Cross-checking from the startup
session resolved the discrepancy. Those earlier reports are superseded; no code
or permission changes were required to fix an application startup failure.

One real migration omission was corrected: the hidden six-day renewal task still
targeted the legacy EXE. The current task now targets 0.2.4 with the explicit private
profile, normal permissions and logon/15-minute triggers; the legacy task is disabled.
The verified three-account refresh receipt was synchronized only after current
phone, account and IPA content verification, preserving its original completion
time. A scheduled check exited successfully with a future due date and no device
writes. This task was migrated after boot; its periodic execution was tested,
while its new logon trigger has not had a second physical reboot test.

## 0.2.4 packaged local repair — 2026-10-23

- 345 isolated tests and Ruff lint/format pass. All 58 frozen application/entry
  modules match source and all 17 packaged checks pass.
- Direct LAN RemotePairing now supplies the local route when legacy lockdown
  installation-service access closes. The packaged private worker renewed three
  apps in 87.67 seconds over local Wi-Fi without USB: 161,198 profile bytes,
  three verified readbacks, zero reinstalls. The owner fully reopened all three
  apps and confirmed successful launches.
- Local request ownership prevents the persistent daemon from claiming the same
  operation. Tests ensure only preflight failures permit route fallback; submitted
  failures propagate without replay. Temporary service failures preserve an open
  roaming transport and withdraw ready status until health checks recover.
- The private bot and tunnel are running the new EXE with the original profile.
  Credentials and assignments were retained; rollback copies remain private.

Known gates: fresh cellular tunnel acquisition is still refused by the phone;
home acquisition followed by warm cellular roaming is the verified workflow.
The earlier private startup diagnosis below is superseded by the real reboot
acceptance above. Startup targets run at normal user permissions.
No code signing or public publication occurred.

The latest expiry acceptance window is 30 October 18:54:20 through 20:20:52
Europe/Rome, without an intervening reinstall. Immediate app launches and stored
profile readback do not establish behavior after the previous profiles expire.

## Initial 0.2.4 source investigation (before the packaged repair above)

Read-only real-device checks found multicast RemotePairing discovery returning
zero answers while a direct DNS-SD request to the authenticated LAN phone found
a listener with a valid pairing authentication tag. The returned listener passed
RemotePairing, RSD identity and installation-service lookup through Tailscale.
The saved listener port was stale. A fallback now handles both empty multicast
results and transport failures, with bounded requests, exact peer/transaction
checks, existing pairing-tag validation and endpoint authentication.

With USB unplugged, fresh home-Wi-Fi Tailscale access passed; an isolated source
daemon kept repeated installation-service lookups ready after switching to mobile
data for a 45-second observation. A separate fresh connection on mobile data was
refused. Direct LAN identity passed, but its installation-service connection
closed. These last two routes remain unresolved; no app signing, refresh or
installation occurred during this follow-up. USB was used only for diagnostic
service access and trusted remote-pair verification before being unplugged.

340 device-free tests and Ruff lint/format passed, including authentic and forged
DNS-SD pairing tags, unrelated senders/transactions, malformed/expired responses,
missing records and fallback endpoint authentication without new pairing.
This is a source/isolated-daemon result. The saved 0.2.3 installer and the owner's
running private bot were not replaced; 0.2.4 has not been packaged or deployed.
The older private daemon's saved ready status was stale and its historical log
reported dependency-loading failures; that status was not accepted as live health.

Validation date: 2026-10-22. Target: Windows x64, Python 3.14.8.

## Checks performed

- Clean Python virtual environment installed from the resolved lock.
- Device-free regression suite: 212 tests passed, including empty onboarding,
  first-account enrollment, saved assignment consistency, private Discord controls,
  scheduler recovery, profile fallback and release boundaries.
- Ruff lint and formatting checks; native Rust formatting and release compilation.
- Native helper authentication protocol rejected invalid input before network access.
- Packaged runtime: all 14 checks passed; the receipt records the executable SHA-256.
- Release-notice collection rejects bytecode, code files, and private build paths.
- Companion executable hashes and embedded build-profile path checks passed.
- Public source allowlist audit passed without live configuration or private assets.
- GPL transport sources default to userspace mode without importing Wintun.
- The matching source archive accompanies the Windows distribution; upstream GPL
  and LGPL source and license notices are included.
- GUI construction and live settings bindings exercised by Tk tests. A desktop
  screenshot inspection was limited by an existing Windows firewall permission
  dialog; no firewall or security permissions were changed.

Packaged runtime and archive results are written to `dist/self-test.json` and
`artifacts/source-audit.json` during the release build. Their presence and successful
status are required by the packaging tool. Review the actual receipts with this file.

## Acceptance still required before a stable release

The original private installation has prior real-device results. Those results
are not a substitute for acceptance of this public EXE and its new onboarding.
This build has not performed a real Apple sign-in, USB install, Wi-Fi install,
cellular install, new-user RemotePairing, reboot/startup test, six-day unattended
cycle, or app-launch check after the old provisioning profiles expire.

RemotePairing setup and local verification prompts need testing on a clean user
profile with a consenting device. Profile readback alone does not prove long-term
renewal compatibility. The release is unsigned and is labeled **preview**, rather
than presenting those remaining checks as completed.

The preparation did not replace the existing private release or change its bot,
scheduled tasks, remote tunnel, device state or private configuration. No GitHub
repository was created and no artifacts were published.


## 0.1.1 authentication retry fix

The native backend now retries transient authentication request failures and login
timeouts before signing or installation. Regression tests cover retryable request
errors and rejection of credential, verification and provisioning failures.
Release builds run those native tests as well as the Python regression suite and
packaged self-test.

The corresponding private development build completed a three-account profile
refresh on home Wi-Fi, with profile readback verified for each account and zero
full installations. This verifies the deployed private path; the public build's
remaining clean-machine and long-term acceptance requirements above still apply.

## 0.1.2 update and support controls

Validated on 2026-10-23 using the clean public build environment:

- 233 device-free tests passed, including actual Tk event-loop responsiveness,
  Discord command authorization, official release-link validation, numeric version
  comparison, caching, rate limits and offline failures.
- Support snapshot tests inject private values into settings and exceptions and
  verify they are absent from the report. Corrupt sections do not hide other results.
- A live read-only GitHub release check returned the current public release.
- All 15 packaged self-tests passed; the receipt identifies version 0.1.2 and the
  exact EXE hash. Companion integrity and source allowlist checks passed.
- The native signing and installation backend is unchanged from 0.1.1. No device
  installations, running bot replacement or tunnel restart were performed for
  these UI and reporting changes. The acceptance limits above remain open.

## 0.2.0 workflow preview

Validated on 2026-10-23 in the clean public build environment:

- 268 device-free tests passed. Coverage includes queue FIFO order, exclusion
  with active work, recovery after a worker exits, changed-file rejection,
  interruption pausing, partial-account retries, conservative schedule recovery,
  backup privacy/restore validation, connection metrics, fresh-profile CLI startup,
  actual Tk controls and startup migration/rollback faults.
- All 17 checks passed in the frozen EXE. A bytecode comparison verified all
  51 bundled application modules against the saved source, with filenames
  normalized solely for comparison. Companion integrity checks passed.
- The updater downloaded the real public 0.1.2 release into an isolated folder,
  verified Windows/source archives and checksums, and successfully ran that EXE's
  15 self-tests. It did not activate that download or change startup tasks.
- A packaged handover test used two temporary copies and a unique empty profile.
  The old GUI exited, the replacement stayed running, rollback reopened the
  previous GUI, and the rollback receipt was retained. Installed community task
  definitions were hashed before and after and were unchanged.
- Task-action migration with matching ownership/profile, trigger preservation,
  normal permissions and failure rollback is covered with isolated COM fixtures.

The native signing/installation backend remains unchanged. No real-device
installation, live migration of the user's startup tasks, physical PC reboot,
roaming tunnel restart or seven-day expiry acceptance was performed. Those
checks remain required before making stable-release reliability claims. The
0.2.0 preview is published with matching source and checksums. Both independent
GitHub Windows validation runs passed for release commit
`64a00d1f3b08f01ab95717d3be8f0b9a9e84a740`.

## 0.2.1 local hardening candidate — not published

Validated locally on 2026-10-23:

- 292 isolated Python tests passed, including changed-content snapshots, wrong
  renewal identities, offline queue deferral, transient contention, failed
  replacement startup, old-history outcomes, maintenance protection, future-clock
  receipts, GUI responsiveness, log rotation and release/advisory controls.
- Two native authentication retry tests and seven isideload library tests passed;
  one optional local disk benchmark remains intentionally ignored. Native tests
  include unsafe archive paths and Windows aliases.
- Ruff lint/formatting, Rust formatting, companion integrity and source audits passed.
- All 17 packaged self-checks passed. All 54 bundled application/entry modules
  matched saved source bytecode after filename normalization.
- A real packaged GUI handover and rollback passed using temporary copies and a
  unique empty profile. Installed community task definitions were unchanged.
- OSV checked 518 locked dependency records, including patched source packages.
  No unhandled matches remained. RSA 0.9.10 has one documented exception expiring
  2026-11-23; upstream has no patched version listed. This is not a claim that the
  program has received an independent security certification.
- Private-data scans covered source, release archives and frozen application
  constants against 70 known private-value patterns without findings.

This candidate remains local. It did not replace the installed application,
restart its private bot/tunnel, change installed tasks, or operate the iPhone.
Local-network renewal is covered with isolated protocol fixtures; live device,
physical reboot, clean-machine, accessibility, roaming and old-profile-expiration
acceptance remain outstanding for this build. Code signing and commercial
service/policy sign-off are separate release requirements. Discord sign-in was
preserved at the owner's explicit request.

## 0.2.2 local recovery candidate — not published

Validated locally on 2026-10-23:

- 301 device-free Python tests passed, with Ruff lint and formatting checks.
- New regression coverage exercises legacy success identity recovery, changed-phone
  invalidation, missing legacy evidence, invalid selections, desktop/background
  history seeding and rejection of future-dated results.
- Scheduler checks avoid network access when scheduled, busy or blocked on an
  unresolved job. Connectivity failures defer without creating a pending job.
- Queue tests simulate repeated probe/tunnel-start failures followed by recovery;
  the original request stays queued, then starts once and clears its waiting reason.
- A month-overdue schedule becomes due once; a verified completion schedules the
  next interval from completion rather than replaying missed calendar intervals.
- Malformed saved device identities are rejected without crashing outcome or
  renewal verification.

These are isolated regression results, not physical reboot or device-expiry
acceptance. The native backend and dependencies are unchanged from 0.2.1. The
existing login flow, installed tasks, running bot and device state were preserved.

## 0.2.3 local setup and recovery candidate — not published

Validated locally on 2026-10-23:

- 330 isolated Python tests passed; lint/format checks passed. The test harness
  finalizes closed Tk fixtures on the main thread before later asynchronous tests.
- Real Tk checklist rendering and responsiveness passed. The same checklist is
  served by Discord `/check`; it contains static findings without private values.
- Installer tests cover verified copies, version preservation, repair rollback,
  ownership boundaries, active-process blocking and preservation of unknown files
  and private settings. A real temporary Start menu shortcut and unique current-user
  registry entry were created and removed. Installed task ownership filtering was
  exercised with isolated COM fixtures.
- Guided update tests cover restoring managed-installation integration after failed
  startup. Process matching excludes other settings profiles; remote daemon mutexes
  are scoped by profile while installation exclusion remains shared.
- All 17 frozen checks passed; all 57 application/entry modules match saved source.
  Packaged GUI handover and rollback passed without changing installed task definitions.
- The setup launcher compiled with an `asInvoker` manifest. Its embedded archive
  extraction and runtime checks passed without installing a desktop copy.

A consenting device test established paired LAN identity, but the phone closed
the direct installation-service lookup before signing or profile writes. The saved
remote-pairing listener also refused connections. A read-only preflight fallback
was regression-tested; failures after submission cannot replay via another route.
Remote recovery remains unverified.

After trusted USB reconnection, the packaged worker renewed all three accounts in
49.48 seconds, transferring 161,194 profile bytes without reinstalling an app.
All three profile readbacks verified, and the owner confirmed that YouTube,
Spotify and Twitch launched after fully closing and reopening them. The tested
EXE hash is recorded in the local acceptance receipt. Subsequent COM lifetime,
notification shortcut and Discord preflight fixes were verified separately; the
final delivery EXE did not repeat this device operation.

An ordinary-user temporary scheduled task successfully ran the packaged checks
without elevation and was removed without changing existing task definitions.
Native COM initialization preserves caller-owned apartments and existing proxies.
Notifications preserve installed shortcut targets and explicit profile arguments.

Additional regression coverage verifies USB refresh priority without network
discovery, original pairing-cache identifier resolution despite case/hyphen
formatting, and refusal of ambiguous matching pairing records.

The native engine and dependency inputs are unchanged from local 0.2.1; the existing
audit and its expiring RSA exception are retained. The installer is unsigned. No
clean physical PC, physical reboot, cellular recovery or old-profile-expiration
acceptance is claimed. The candidate remains local and has not replaced the
owner's existing deployment or been published.
