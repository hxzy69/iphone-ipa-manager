# Block buffer dependency assessment

The OSV dependency audit reports [GHSA-qwgh-2vcv-g2f7](https://github.com/RustCrypto/utils/security/advisories/GHSA-qwgh-2vcv-g2f7) for `block-buffer` 0.10.4. The advisory's published affected-version range includes this version; the audit must continue to report it until the range is corrected or the dependency is replaced with a supported fix.

The upstream description concerns a buffer whose cursor is stored inside its data array. The 0.10.4 source instead has a separate `pos` field and does not expose the `ReadBuffer` type described in that advisory. The regression tests in `native/tests/block_buffer_advisory.rs` exercise the compression-panic scenario and the analogous 0.10.4 generation-panic operation. Both passed under the normal Rust test runner and under Miri on Windows on 27 October 2026.

These observations are evidence about the described mechanism, not a proof that the library or the complete application has no memory-safety defect. The Miri run covered only the two regression scenarios, not every possible buffer operation or the application as a whole. This finding is not suppressed in `third_party/security-exceptions.json`.

The release native worker is additionally compiled with `panic = "abort"`. A Rust panic ends that worker instead of unwinding and allowing the process to reuse potentially inconsistent state. The controller must treat an interrupted operation as uncertain and must not automatically replay it. This is defense in depth, not an upstream patch or an assurance against other defects; the advisory remains open.

The project also vendors 0.10.4 with a local hardening patch: reading or writing an invalid cursor triggers a checked assertion even in optimized builds, rather than invoking `unreachable_unchecked`. The original MIT and Apache licenses are included alongside the modified source. Tests exercise both invalid cursor paths in release mode, and the dependency audit continues to query this patched version against OSV. This local change is not the upstream 0.12.1 release and has not received an independent security review; the finding therefore remains open.

`digest` 0.10 requires `block-buffer` 0.10, and several native dependencies require that generation of `digest`. Upgrading `block-buffer` alone to 0.12.1 fails dependency resolution. Replacing this stack needs compatible upstream releases or an independently reviewed migration, followed by signing and real-device installation tests.
