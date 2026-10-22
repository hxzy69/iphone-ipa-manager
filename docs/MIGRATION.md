# Migrating an existing private installation

The community build deliberately starts empty and uses a separate state folder
and task names. It does not copy an existing bot token, accounts or device settings.
The native Credential Manager service and optional fixed-purpose bridge service
remain compatible with iloader; they are shared Windows resources, not sandboxed
test resources.

1. Stop active installs and disable the old installation's bot, remote and renewal
   startup tasks. Keep a private backup; do not commit or publish it.
2. Place the community EXE in its permanent folder. Use its UI to select the IPA
   folder, device and accounts. Enter the bot token locally if Discord is wanted.
3. Re-establish and verify remote pairing before enabling renewal. Avoid two bots
   using the same token concurrently, and avoid two owners sharing one bridge.
4. Enable the new startup/renewal settings only after manual verification. The
   community tasks are named `iPhone IPA Community Discord`, `iPhone IPA Community
   Remote Tunnel`, and `iPhone IPA Community Automatic Refresh`.

No automatic migration of renewal timestamps is attempted. The first verified
all-account installation/renewal establishes the new timer. Keep the original
working installation available until the new build is verified with your device.

