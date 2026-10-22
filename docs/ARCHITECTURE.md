# Runtime architecture

The desktop app, Discord bot and renewal scheduler are controllers around the
same configuration and job protocol. They do not each maintain an account list.

## State and processes

`paths.py` separates immutable assets from private per-user state. `config.py`
reads fail-closed configuration; `storage.py` replaces complete files atomically.
An absent configuration starts onboarding. A malformed existing configuration
must be repaired rather than overwritten with defaults.

`ipa_manager.py` dispatches GUI and worker modes. Each job has an isolated folder
under `automation-runs/`, a stop marker, progress events and a final receipt.
`worker_registry.py` tracks process creation time as well as PID to avoid stale
process identity. `run_lock.py` serializes account authentication, installation
and assignment changes across controllers.

The native Rust helper handles Apple authentication, signing and archive export.
It is a private child process, not a network daemon or arbitrary command runner.
Authentication requests travel through stdin/stdout JSON, with bounded prompts
and verified process completion. Its authentication failure output is deliberately
generic; credentials never belong in command arguments or job files.

## Assignments and installation

An account number is its one-based position in the saved list. The managed folder
contains complete IPA files. Assignment validates the archive and snapshots its
name, size and modification time; a changed file or account mapping invalidates
an already-requested run. New assignments are marked pending until a verified
install acknowledges the exact current assignment.

`background.py` submits native jobs. `connection_router.py` prefers a locally
reachable device only after authenticating against its existing pairing identity.
The local broker has a fixed pipe protocol and restores Apple's USB service when
its lease ends. Remote jobs use a persistent, authenticated RemotePairing tunnel
and a serialized queue; a Tailscale IP alone is not device authentication.

## Renewal and failure boundaries

`profile_refresh.py` checks installed profiles and current signing identity.
Successful transfer requires exact profile readback. A pending assignment,
confirmed profile rejection, expiry or readback mismatch can request one complete
archive installation. Unknown write/transport outcomes stop; they do not authorize
a duplicate install.

`auto_refresh.py` measures six days from verified all-account completion. It
checks at sign-in and every 15 minutes, defers while unavailable/busy, and pauses
an ambiguous submitted run. It can recover a valid receipt written before a
controller crash without replaying device work. Partial success does not reset
the global schedule. `discord_outbox.py` persists owner-bound operational reports;
a crash between send and acknowledgement can duplicate a report, not an install.

## UI consistency

`gui_sync.py` performs a three-way merge between the last saved values, local
edits and newly persisted values. Untouched controls follow external changes.
Conflicts are displayed and block save until resolved. New Discord-enrolled
accounts append cards dynamically; assignments do not silently replace slots.

## Validation scope

Unit and contract tests are device-free and run with a temporary data directory.
Packaged self-tests check imports, atomic storage, mutexes, input rejection and
the bundled native protocol. They do not prove hardware compatibility, successful
Apple authentication, app launch after expiry, or uninterrupted remote roaming.
Record those acceptance checks separately for each release candidate.
