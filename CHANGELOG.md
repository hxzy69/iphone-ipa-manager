# Changelog

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

