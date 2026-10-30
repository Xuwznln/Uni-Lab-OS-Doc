//! 调度引擎公共值协议、生命周期与串行策略；不依赖解释器或宿主对象。
pub mod protocol;
pub mod queue;
pub mod runtime;
pub mod validation;
pub use serde_json;

/// 六个公开 C ABI 符号；原始响应必须由本库释放。
#[macro_export]
macro_rules! export_engine {
    ($algorithm:ty) => {
        fn native_call(operation: &str, request: &[u8]) -> Vec<u8> {
            static ENGINE: std::sync::OnceLock<
                std::sync::Mutex<$crate::runtime::Runtime<$algorithm>>,
            > = std::sync::OnceLock::new();
            let engine = ENGINE.get_or_init(|| {
                std::sync::Mutex::new($crate::runtime::Runtime::new(
                    <$algorithm>::default(),
                    env!("UNILAB_BUILD_ID"),
                    env!("CARGO_PKG_VERSION"),
                ))
            });
            match engine.try_lock() {
                Ok(mut engine) => engine.call(operation, request),
                Err(std::sync::TryLockError::Poisoned(_)) => $crate::protocol::failure_bytes(
                    request,
                    $crate::protocol::Error::new(
                        "ENGINE_INTERNAL",
                        "引擎状态因异常失效，必须重建 worker",
                    ),
                ),
                Err(std::sync::TryLockError::WouldBlock) => $crate::protocol::failure_bytes(
                    request,
                    $crate::protocol::Error::new("INSTANCE_BUSY", "引擎已有调用正在执行")
                        .retryable(),
                ),
            }
        }
        unsafe fn invoke(
            operation: &str,
            req: *const u8,
            len: u64,
            out: *mut *mut u8,
            out_len: *mut u64,
        ) -> i32 {
            if !out.is_null() {
                *out = std::ptr::null_mut();
            }
            if !out_len.is_null() {
                *out_len = 0;
            }
            if out.is_null()
                || out_len.is_null()
                || req.is_null()
                || len == 0
                || len > $crate::protocol::MAX_BYTES as u64
            {
                return -1;
            }
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                native_call(operation, std::slice::from_raw_parts(req, len as usize))
            })) {
                Ok(bytes) if !bytes.is_empty() && bytes.len() <= $crate::protocol::MAX_BYTES => {
                    let mut bytes = bytes.into_boxed_slice();
                    *out_len = bytes.len() as u64;
                    *out = bytes.as_mut_ptr();
                    std::mem::forget(bytes);
                    0
                }
                _ => -3,
            }
        }
        #[no_mangle]
        pub extern "C" fn uni_sched_abi_version() -> u32 {
            0x00010000
        }
        #[no_mangle]
        pub unsafe extern "C" fn uni_sched_describe(
            r: *const u8,
            n: u64,
            o: *mut *mut u8,
            l: *mut u64,
        ) -> i32 {
            invoke("describe", r, n, o, l)
        }
        #[no_mangle]
        pub unsafe extern "C" fn uni_sched_create(
            r: *const u8,
            n: u64,
            o: *mut *mut u8,
            l: *mut u64,
        ) -> i32 {
            invoke("create", r, n, o, l)
        }
        #[no_mangle]
        pub unsafe extern "C" fn uni_sched_decide(
            r: *const u8,
            n: u64,
            o: *mut *mut u8,
            l: *mut u64,
        ) -> i32 {
            invoke("decide", r, n, o, l)
        }
        #[no_mangle]
        pub unsafe extern "C" fn uni_sched_close(
            r: *const u8,
            n: u64,
            o: *mut *mut u8,
            l: *mut u64,
        ) -> i32 {
            invoke("close", r, n, o, l)
        }
        #[no_mangle]
        pub unsafe extern "C" fn uni_sched_free_buffer(p: *mut u8, n: u64) {
            if !p.is_null() && n > 0 && n <= $crate::protocol::MAX_BYTES as u64 {
                drop(Box::from_raw(std::ptr::slice_from_raw_parts_mut(
                    p, n as usize,
                )));
            }
        }
    };
}
