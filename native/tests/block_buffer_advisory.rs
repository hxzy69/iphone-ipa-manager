//! Check the panic scenarios described in GHSA-qwgh-2vcv-g2f7 against 0.10.4.
//!
//! This regression test does not establish that the advisory is inapplicable.
//! The upstream version range still requires a separate security assessment.

use block_buffer::{EagerBuffer, generic_array::typenum::U4};
use std::panic::{AssertUnwindSafe, catch_unwind};

#[test]
fn compression_panic_preserves_cursor_in_the_locked_legacy_version() {
    let mut buffer = EagerBuffer::<U4>::new(&[1, 2]);
    let result = catch_unwind(AssertUnwindSafe(|| {
        buffer.digest_blocks(&[3, 0xff], |_| panic!("simulated compression failure"));
    }));
    assert!(result.is_err());
    assert_eq!(buffer.get_pos(), 2);
}

#[test]
fn generator_panic_preserves_cursor_in_the_locked_legacy_version() {
    let mut buffer = EagerBuffer::<U4>::new(&[1, 2]);
    let mut output = [0; 8];
    let result = catch_unwind(AssertUnwindSafe(|| {
        buffer.set_data(&mut output, |blocks| {
            blocks[0][0] = 0xff;
            panic!("simulated generation failure");
        });
    }));
    assert!(result.is_err());
    assert_eq!(buffer.get_pos(), 2);
}
