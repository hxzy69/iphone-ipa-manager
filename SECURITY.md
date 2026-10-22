# Security and private data

This application can access a trusted iPhone and signing accounts. Run it only
under the Windows user intended to own those credentials. Keep the Discord server
private, use the owner/server/channel restrictions, and protect the PC account.

- Discord tokens use Windows DPAPI for the current user. Apple credentials use
  the native helper's Windows Credential Manager integration (`iloader` service).
- Local sign-in passes credentials through an anonymous subprocess pipe. Discord
  sign-in additionally exposes submitted form contents to Discord's infrastructure.
- Accounts, device serials, paths, job history and logs are private runtime state.
  Pairing keys live in Apple/pymobiledevice3 data directories outside this source tree.
- Third-party anisette services are used during Apple authentication. The default
  comes from the upstream integration; review that trust relationship before use.
- The optional privileged bridge accepts a fixed protocol and requires separate
  administrator installation. The application never automatically requests UAC.

Do not post secrets in public issues. There is no hosted security-reporting
endpoint configured in this source distribution. Contact the eventual repository
maintainer privately using the hosting platform's private vulnerability reporting
if enabled. Rotate an exposed bot token/password, revoke affected sessions, and
re-pair the device if pairing records were disclosed.

The release audit checks common secret patterns, private file types and user
paths. It is a release guard, not proof that all possible secrets or vulnerabilities
have been found. Dependency updates and independent security review remain necessary.

