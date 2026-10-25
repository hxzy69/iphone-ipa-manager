# Commercial release acceptance

0.2.10 is a private release candidate. Do not describe its device tests as a
certification or copy acceptance from another executable into its release record.

Run the device-free suite, packaged self-test, companion verification, source
privacy audit, dependency audit and archive checks before acceptance. The build
instructions remain in BUILDING.md. Keep credentials and raw device reports out
of distribution archives.

## Windows lifecycle test

From a configured development environment:

```powershell
python tools/smoke_desktop.py dist --report artifacts/desktop-acceptance.json
```

The test uses a disposable application/settings folder, unique current-user
registry entry and unique normal-permission scheduled task. It installs the real
payload, repairs it, migrates a hidden task, runs the installed self-test and
uninstalls. It verifies that private settings and an unowned file survive, while
owned binaries, shortcut, registry entry and hidden task are removed. It does not
run an install or renewal on an iPhone. Passing it on a development PC does not
constitute a clean-Windows test.

Run `python tools/smoke_recovery.py dist/iPhoneIPAManager.exe --report artifacts/recovery-acceptance.json`
to exercise missing files, mismatched completion receipts, corrupt queue state
and corrupt renewal state against the packaged executable. It uses disposable
fake inputs and verifies no device work is submitted.

## Candidate evidence

Record the scenarios listed in `tools/release_readiness.py` against the final
candidate executable. Include setup with a new user, supported device routes,
roaming/recovery, reboot with overdue renewal, accessibility and app launches
after the previous profiles expire. A Wi-Fi-acquired tunnel roaming onto cellular
is supported; fresh acquisition on cellular remains an unverified capability and
must not be advertised as supported.

An acceptance JSON uses this form for each scenario:

```json
{
  "acceptance": {
    "local_wifi_install_and_refresh": {
      "ok": true,
      "exe_sha256": "SHA-256 of the final candidate",
      "tested_at": "ISO-8601 timestamp with timezone",
      "evidence": "Reference to the retained QA report"
    }
  }
}
```

Sign the executable and installer using the release publisher's code-signing
identity. Refresh the packaged self-test and archive checksums after signing;
signatures change executable bytes. Verify the final signed build, not an earlier
unsigned candidate. No signing identity is bundled with the project.

```powershell
python tools/release_readiness.py dist/iPhoneIPAManager.exe artifacts/iPhoneIPAManager-0.2.10-setup-x64.exe --audit artifacts/dependency-audit.json --evidence acceptance.json --report artifacts/commercial-readiness.json
```

The checker exits unsuccessfully for missing or invalid signatures, stale
dependency checks, missing acceptance or evidence belonging to another executable.
Accepted dependency advisories require a recorded commercial dependency review.
The reports are release records, not substitutes for independent security review,
license compliance, an identified publisher or a customer support process.

The expiry and long-duration tests require elapsed time. Preserve their actual
results and limitations; do not mark them complete from immediate app launches.
