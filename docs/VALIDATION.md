# Public preview validation

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

## 0.2.0 workflow candidate

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
published download remains 0.1.2 while this candidate receives independent CI.
