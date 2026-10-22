# Contributing

Open an issue with a clear reproduction, Windows/Python/app version, connection
type and a redacted error. Do not attach live configuration, IPA archives,
credentials, provisioning profiles, pair records, or unreviewed logs.

Keep changes focused and readable. Use explicit sample accounts in tests; a new
user must start with no accounts. All controllers must use the same atomic state
and worker exclusion. Device mutations require verified receipts; uncertain
outcomes must not trigger automatic duplicate installs.

Follow [BUILDING.md](docs/BUILDING.md), run the device-free suite and formatting
checks, and document any manual device testing separately. Never run a real
installation from CI. Do not add automatic elevation, arbitrary remote shell
commands, secret-bearing command arguments, or silent configuration resets.

Native changes belong in `native/`; signing-library patches belong in
`vendor/isideload/`. Preserve upstream license notices and summarize vendor
changes in `vendor/README.md`. Include compatibility and migration notes for
worker protocols or persisted state changes.

