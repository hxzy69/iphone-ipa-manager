# Guided setup, run review and progress

These features are in the source development build. They are not part of the
published 0.3.6 download yet.

## Guided setup

Open **Guided setup** on Overview. The six steps lead through choosing an IPA
folder, pairing an unlocked iPhone over USB, adding Apple accounts, checking
readiness, optional remote/Discord setup, and choosing automatic renewal.

The wizard opens the existing tools and uses their saved settings. Saving a
folder uses the same protected preference update as Discord. Nothing is enabled
automatically. Use **Recheck** after completing a step. Pairing saved on disk is
not proof of a live connection; the first installation is a separate operation.
Remote access and Discord are optional for USB use.

Discord `/setup` shows these steps and their current saved-state checks. Initial
USB trust, Apple verification and remote-pairing setup still require the normal
user interactions. The wizard does not bypass these requirements.

## Review changes before running

Desktop **Install apps**, network installs, and dashboard queue submissions show
a review with account, actual app name, version, filename and expected operation.
Select a row for its complete account and filename. **Reload review** rereads
saved settings after a file or assignment change. Unsaved main-window edits are
not added to a review that is already open.

Discord `/sideload`, `/refresh`, picker **Install now**, and `/queue` install or
refresh show a private review with **Confirm and start** and **Cancel**. Queued
work uses **Confirm and queue**. A review is single-use and expires after three
minutes. Long Discord labels are shortened
to fit message limits; `/inbox` lets you choose the full filename.

Changing the phone, account scope, assignments, folder or file signature requires
a fresh review. The worker checks the plan again before device work, and the
existing archive/content checks remain authoritative. A review is a metadata
summary, not proof that an app is trustworthy or that the phone is reachable.

Refresh shows a full install for pending IPA changes. Otherwise it shows profile
refresh **if eligible**, because phone-side eligibility and fallback cannot be
guaranteed in advance. Refresh continues to cover all configured accounts.

Automatic six-day renewal remains unattended. It uses saved assignments and the
existing eligibility, pending-install and connection checks; it does not wait for
someone to press a confirmation button.

## Live progress

Open **Live progress** on Overview or use Discord `/progress`. It shows each
account's reported stage and elapsed time. Signing in, signing, transferring,
installing and verifying are distinct stages when the backend reports them.
Profile renewal can skip stages. The UI does not invent percentages or an ETA.

Preparing files is not a verified installation. Cancelled and failed results
provide next-step guidance; use **Activity & recovery** or `/dashboard` to inspect
verified account outcomes before retrying an uncertain operation.

The GUI preview includes sample setup, review, progress and assignment-history
windows. It does not save preferences, contact a phone or start real jobs.
