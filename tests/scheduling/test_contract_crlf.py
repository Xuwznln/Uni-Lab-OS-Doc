"""Windows CRLF 检出仍须验证以 LF 规范化的契约摘要。"""

from __future__ import annotations

import shutil

from unilabos.scheduling.plugin import contract_file_digest, verify_contract
from unilabos.scheduling.protocol import CONTRACT_DIR, CONTRACT_SHA256


def test_crlf_contract_files_verify_against_lf_manifest(tmp_path):
    copied = tmp_path / "contract"
    shutil.copytree(CONTRACT_DIR, copied)
    for path in copied.rglob("*"):
        if path.is_file():
            lf = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            path.write_bytes(lf.replace(b"\n", b"\r\n"))
    verify_contract(copied, CONTRACT_SHA256)


def test_lf_and_crlf_bytes_share_the_same_digest():
    raw = b"alpha\nbeta\n"
    assert contract_file_digest(raw) == contract_file_digest(raw.replace(b"\n", b"\r\n"))
    assert contract_file_digest(raw) != contract_file_digest(b"tampered\n")
