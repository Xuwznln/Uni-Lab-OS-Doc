// 协议 r3 是已批准附件；构建时校验全部文件，不能编译局部改动的同名协议。
fn verify_contract(crate_root: &std::path::Path) {
    use sha2::{Digest, Sha256};
    let root = crate_root.join("../../unilabos/scheduling/contract");
    let mut manifest: serde_json::Value = serde_json::from_slice(
        &std::fs::read(root.join("scheduler-engine-v1-contract.manifest.json")).unwrap(),
    )
    .unwrap();
    let approved = "972d73fe8ee3c03d4ca91c19699d3d00db67b566d5b28550d708405d20a31a13";
    assert_eq!(manifest["contract_sha256"], approved, "协议版本尚未批准");
    for (name, expected) in manifest["files"].as_object().unwrap() {
        let text = std::fs::read_to_string(root.join(name))
            .unwrap()
            .replace("\r\n", "\n")
            .replace('\r', "\n");
        assert_eq!(
            format!("{:x}", Sha256::digest(text.as_bytes())),
            expected.as_str().unwrap(),
            "协议附件摘要不匹配：{name}"
        );
    }
    manifest.as_object_mut().unwrap().remove("contract_sha256");
    assert_eq!(
        format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&manifest).unwrap())
        ),
        approved,
        "协议清单摘要不匹配"
    );
    println!("cargo:rerun-if-changed=../scheduler_shared/contract_build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=Cargo.lock");
}
