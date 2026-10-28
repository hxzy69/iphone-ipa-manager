# Vendored signing library

`isideload/` originates from the MIT-licensed `isideload` 0.4.6 crate by Nicholas
Sharp. Its original license is retained. It is a local Cargo patch rather than an
unreviewed download during application startup.

Local changes support background progress, signing/profile export, conservative
full-install handshakes, archive compression/transfer and app-specific account
jobs. `native/` integrates the headless portions derived from iloader. Dependency
versions are fixed in `native/Cargo.lock`. No iloader frontend assets are shipped.

Review vendor updates separately from application changes, retain attribution,
and rerun native protocol and device-level acceptance checks. This directory
contains source only; target directories, registry metadata and local credentials
do not belong here.

## GPL and LGPL sources

`python/` contains the exact installed sources for pymobiledevice3 11.38.0,
pmd-pytcp 0.3.8, pmd-net-addr 0.0.3 and pmd-net-proto 0.0.3. Their GPL-3.0-or-later
licenses are retained. Transport patches default to userspace networking
and defer the unused kernel-TUN import, allowing the release to omit Wintun.
Network lockdown sessions and their service connections require TLS and verify
the device certificate against the saved Apple pairing record on each connection.
The async TLS transport closes channels that fail this explicit certificate pin.
Services without authenticated TLS are rejected on network lockdown transports;
the application can use its authenticated RemotePairing fallback instead.
`python/SOURCES.json` records versions and the patch. These sources take precedence
in source runs, tests, and the PyInstaller analysis.

`apple-codesign-quick/` is the unmodified 0.1.0 registry source under
LGPL-2.1-or-later. Its full license and revision provenance accompany it. Cargo
uses this local patch by default, so edits to it are compiled into a rebuilt native
helper. The matching full application source ZIP is included in the Windows
archive; users can modify and rebuild the helper and the combined application.
