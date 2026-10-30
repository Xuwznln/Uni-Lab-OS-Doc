include!("contract_build.rs");
use std::{
    env,
    time::{SystemTime, UNIX_EPOCH},
};
fn main() {
    verify_contract(std::path::Path::new(
        &env::var("CARGO_MANIFEST_DIR").unwrap(),
    ));
    let crate_name = env::var("CARGO_PKG_NAME").unwrap();
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    println!("cargo:rustc-env=UNILAB_BUILD_ID={crate_name}-rust-{stamp}");
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=../scheduler_shared/src");
    println!("cargo:rerun-if-changed=../scheduler_shared/build_native.rs");
    println!("cargo:rerun-if-changed=../../unilabos/scheduling/contract");
}
