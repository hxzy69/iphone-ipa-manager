# Third-party software

iPhone IPA Manager is an independent project. Apple, Discord, Tailscale, and the
upstream projects below do not sponsor or endorse it. No Apple credentials,
device pairing records, IPA files, or user accounts are included.

| Component | Origin | License / location |
| --- | --- | --- |
| Native authentication and signing integration | [iloader](https://github.com/nab138/iloader) | MIT; `native/LICENSE` |
| Modified signing and archive transport library | [isideload 0.4.6](https://crates.io/crates/isideload/0.4.6) | MIT; `vendor/isideload/LICENSE` |
| Network USB bridge | [netmuxd 0.4.3](https://github.com/jkcoxson/netmuxd) | MIT; `tools/netmuxd/LICENSE` |
| Python device services and userspace TCP | [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), pmd-pytcp, pmd-net-addr, pmd-net-proto | GPL-3.0-or-later; complete Python package sources in `vendor/python/` |
| Apple code signing | [apple-codesign-quick](https://github.com/Dadoum/apple-crates) | LGPL-2.1-or-later; source and license in `vendor/apple-codesign-quick/` |
| Native iOS device services | [idevice](https://github.com/jkcoxson/idevice) | MIT; generated dependency notices |
| Patched block buffer | [RustCrypto block-buffer 0.10.4](https://crates.io/crates/block-buffer/0.10.4) | MIT or Apache-2.0; `vendor/block-buffer/` |

The native helper is derived from iloader and uses its established credential
storage service name for compatibility. The iloader frontend, icons, and update
mechanism are not included. See [iloader.app](https://iloader.app) for upstream.

## Research and design credit

[JAS](https://github.com/jkcoxson/jas), by jkcoxson and its contributors, informed
the investigation of profile-only renewal and IP-reachable device management.
No JAS source code was copied into this project. The implementation uses the
credited device-service libraries above; JAS is acknowledged as research and
design inspiration rather than a bundled component.

The public application is distributed under GPL-3.0-or-later, matching its
Python transport dependencies. The native helper and other components retain their
MIT, LGPL and other upstream licenses; the root license does not replace them.
The Windows archive includes the matching application source ZIP, the GPL Python
transport sources, and the LGPL signing source. Build and relink instructions are
in `docs/BUILDING.md`; no restrictions are imposed on rebuilding those libraries.

The standalone distribution also contains Python and selected Python/Rust runtime
dependencies. Their notices are collected next to the executable. The dependency
inventories describe the complete build environment and Cargo graph, which can
include packages not present in the EXE. Wintun and iloader frontend assets are
not included. Third-party service names identify integrations only.
