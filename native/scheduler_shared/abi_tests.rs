use super::*;
type Entry = unsafe extern "C" fn(*const u8, u64, *mut *mut u8, *mut u64) -> i32;
const ENTRIES: [Entry; 4] = [
    uni_sched_describe,
    uni_sched_create,
    uni_sched_decide,
    uni_sched_close,
];

#[test]
fn invalid_pointers_and_lengths_never_expose_a_buffer() {
    assert_eq!(uni_sched_abi_version(), 0x00010000);
    for entry in ENTRIES {
        for (pointer, length) in [
            (std::ptr::null(), 1),
            (b"{}".as_ptr(), 0),
            (b"{}".as_ptr(), 16 * 1024 * 1024 + 1),
        ] {
            let mut output = std::ptr::dangling_mut::<u8>();
            let mut size = 123;
            assert!(unsafe { entry(pointer, length, &mut output, &mut size) } < 0);
            assert!(output.is_null());
            assert_eq!(size, 0);
        }
    }
    unsafe { uni_sched_free_buffer(std::ptr::null_mut(), 0) };
}

#[test]
fn malformed_json_returns_owned_error_buffers_and_does_not_poison_runtime() {
    for entry in ENTRIES {
        for raw in [
            b"{".as_slice(),
            b"{\"x\":1,\"x\":2}",
            b"18446744073709551616",
        ] {
            let mut output = std::ptr::null_mut();
            let mut size = 0;
            assert_eq!(
                unsafe { entry(raw.as_ptr(), raw.len() as u64, &mut output, &mut size) },
                0
            );
            let response: unilab_scheduler_core::serde_json::Value = unsafe {
                unilab_scheduler_core::serde_json::from_slice(std::slice::from_raw_parts(
                    output,
                    size as usize,
                ))
                .unwrap()
            };
            unsafe { uni_sched_free_buffer(output, size) };
            assert_eq!(response["error"]["code"], "INVALID_REQUEST");
            assert!(response["operation"].is_null());
        }
    }
}
