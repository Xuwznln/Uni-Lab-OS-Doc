//! 公开串行调度动态库；调度和协议均由 Rust 执行。
unilab_scheduler_core::export_engine!(unilab_scheduler_core::queue::QueueEngine);

#[cfg(test)]
#[path = "../../scheduler_shared/abi_tests.rs"]
mod abi_tests;
