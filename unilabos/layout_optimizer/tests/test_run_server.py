import os
from pathlib import Path

import pytest

from unilabos.config.config import BasicConfig, HTTPConfig

from ..run_server import (
    ADDR_ALIASES,
    DEFAULT_EDGE_BASE,
    DEFAULT_CONFIG_NAME,
    apply_runtime_config,
    discover_config_path,
    load_json_config,
    normalize_remote_addr,
)


@pytest.fixture
def _restore_runtime_config():
    old_ak = BasicConfig.ak
    old_sk = BasicConfig.sk
    old_addr = HTTPConfig.remote_addr
    old_mount = os.getenv("LAYOUT_MOUNT_UUID")
    try:
        yield
    finally:
        BasicConfig.ak = old_ak
        BasicConfig.sk = old_sk
        HTTPConfig.remote_addr = old_addr
        if old_mount is None:
            os.environ.pop("LAYOUT_MOUNT_UUID", None)
        else:
            os.environ["LAYOUT_MOUNT_UUID"] = old_mount


def test_discover_config_path_prefers_explicit(tmp_path: Path):
    p = tmp_path / "my_cfg.json"
    p.write_text("{}", encoding="utf-8")
    assert discover_config_path(str(p), cwd=tmp_path) == p.resolve()


def test_discover_config_path_uses_default_file(tmp_path: Path):
    p = tmp_path / DEFAULT_CONFIG_NAME
    p.write_text("{}", encoding="utf-8")
    assert discover_config_path(None, cwd=tmp_path) == p.resolve()


def test_load_json_config_requires_object(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_json_config(p)


def test_apply_runtime_config_cli_overrides_file(_restore_runtime_config):
    os.environ.pop("LAYOUT_MOUNT_UUID", None)
    effective = apply_runtime_config(
        file_config={
            "ak": "file-ak",
            "sk": "file-sk",
            "addr": "https://file.example/api/v1",
            "mount_uuid": "mount-from-file",
        },
        ak="cli-ak",
        sk="cli-sk",
        addr="https://cli.example/api/v1",
        mount_uuid="mount-from-cli",
    )
    assert BasicConfig.ak == "cli-ak"
    assert BasicConfig.sk == "cli-sk"
    assert HTTPConfig.remote_addr == "https://cli.example/api/v1"
    assert os.getenv("LAYOUT_MOUNT_UUID") == "mount-from-cli"
    assert effective == {
        "ak": "cli-ak",
        "sk": "cli-sk",
        "addr": "https://cli.example/api/v1",
        "mount_uuid": "mount-from-cli",
    }


def test_normalize_remote_addr_aliases_and_suffix():
    assert normalize_remote_addr("test", "") == f"{ADDR_ALIASES['test']}/api/v1"
    assert normalize_remote_addr("uat", "") == f"{ADDR_ALIASES['uat']}/api/v1"
    assert normalize_remote_addr("local", "") == f"{ADDR_ALIASES['local']}/api/v1"
    # full URL without suffix should auto-append /api/v1
    assert normalize_remote_addr("https://edge.example.com", "") == "https://edge.example.com/api/v1"
    # if already has suffix, keep it
    assert normalize_remote_addr("https://edge.example.com/api/v1", "") == "https://edge.example.com/api/v1"


def test_apply_runtime_config_uses_edge_default_when_no_addr(_restore_runtime_config):
    effective = apply_runtime_config(
        file_config={"ak": "a", "sk": "s"},
        ak="",
        sk="",
        addr="",
        mount_uuid="",
    )
    assert effective["addr"] == f"{DEFAULT_EDGE_BASE}/api/v1"

