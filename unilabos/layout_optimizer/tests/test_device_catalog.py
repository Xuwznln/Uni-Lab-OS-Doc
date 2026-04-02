"""device_catalog 双源加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..device_catalog import (
    _DEFAULT_FOOTPRINTS,
    create_devices_from_list,
    load_devices_from_assets,
    load_devices_from_registry,
    load_footprints,
    merge_device_lists,
    reset_footprints_cache,
    resolve_device,
)

# ---------- fixtures ----------

# LeapLab/layout_optimizer/tests/ → LeapLab/ → DPTech/
_LEAPLAB = Path(__file__).resolve().parent.parent.parent
_DPTECH = _LEAPLAB.parent
DATA_JSON = _DPTECH / "uni-lab-assets" / "data.json"
REGISTRY_DIR = _LEAPLAB / "Uni-Lab-OS" / "unilabos" / "device_mesh" / "devices"


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试前清除缓存。"""
    reset_footprints_cache()
    yield
    reset_footprints_cache()


# ---------- footprints ----------


class TestLoadFootprints:
    def test_load_footprints_exists(self):
        fp = load_footprints(_DEFAULT_FOOTPRINTS)
        assert isinstance(fp, dict)
        assert len(fp) > 0

    def test_footprint_structure(self):
        fp = load_footprints()
        for dev_id, entry in fp.items():
            assert "bbox" in entry, f"{dev_id} missing bbox"
            assert len(entry["bbox"]) == 2
            assert "height" in entry
            assert "origin_offset" in entry
            assert "openings" in entry

    def test_known_device_in_footprints(self):
        fp = load_footprints()
        assert "agilent_bravo" in fp
        bbox = fp["agilent_bravo"]["bbox"]
        assert 0.5 < bbox[0] < 1.0  # width ~0.65m
        assert 0.5 < bbox[1] < 1.0  # depth ~0.70m

    def test_nonexistent_path_returns_empty(self):
        reset_footprints_cache()
        fp = load_footprints("/nonexistent/footprints.json")
        assert fp == {}


# ---------- assets 加载 ----------


class TestLoadFromAssets:
    @pytest.mark.skipif(not DATA_JSON.exists(), reason="data.json not found")
    def test_load_returns_devices(self):
        devices = load_devices_from_assets(DATA_JSON)
        assert len(devices) > 0

    @pytest.mark.skipif(not DATA_JSON.exists(), reason="data.json not found")
    def test_known_device_has_real_bbox(self):
        devices = load_devices_from_assets(DATA_JSON)
        bravo = next((d for d in devices if d.id == "agilent_bravo"), None)
        assert bravo is not None
        assert bravo.bbox != (0.6, 0.4)  # 不是默认值
        assert bravo.source == "assets"

    def test_missing_data_json(self):
        devices = load_devices_from_assets("/nonexistent/data.json")
        assert devices == []


# ---------- registry 加载 ----------


class TestLoadFromRegistry:
    @pytest.mark.skipif(not REGISTRY_DIR.exists(), reason="registry dir not found")
    def test_load_returns_devices(self):
        devices = load_devices_from_registry(REGISTRY_DIR)
        assert len(devices) > 0

    @pytest.mark.skipif(not REGISTRY_DIR.exists(), reason="registry dir not found")
    def test_elite_robot_present(self):
        devices = load_devices_from_registry(REGISTRY_DIR)
        elite = next((d for d in devices if d.id == "elite_robot"), None)
        assert elite is not None
        assert elite.source == "registry"

    def test_missing_dir(self):
        devices = load_devices_from_registry("/nonexistent/")
        assert devices == []


# ---------- 合并与去重 ----------


class TestMergeDedup:
    def test_registry_wins_dedup(self):
        from ..models import Device

        reg = [Device(id="ot2", name="OT-2 Registry", bbox=(0.62, 0.50), source="registry")]
        asset = [Device(id="ot2", name="OT-2 Assets", bbox=(0.62, 0.50), source="assets")]
        merged = merge_device_lists(reg, asset)
        ot2 = next(d for d in merged if d.id == "ot2")
        assert ot2.source == "registry"
        assert ot2.name == "OT-2 Registry"

    def test_merge_preserves_unique(self):
        from ..models import Device

        reg = [Device(id="elite", name="Elite", source="registry")]
        asset = [Device(id="bravo", name="Bravo", source="assets")]
        merged = merge_device_lists(reg, asset)
        ids = {d.id for d in merged}
        assert ids == {"elite", "bravo"}

    def test_registry_inherits_asset_model(self):
        from ..models import Device

        reg = [Device(id="ot2", name="OT-2", source="registry", model_path="")]
        asset = [Device(id="ot2", name="OT-2", source="assets", model_path="/models/ot2/mesh.glb")]
        merged = merge_device_lists(reg, asset)
        ot2 = next(d for d in merged if d.id == "ot2")
        assert ot2.model_path == "/models/ot2/mesh.glb"


# ---------- resolve_device ----------


class TestResolveDevice:
    def test_known_device(self):
        dev = resolve_device("agilent_bravo")
        assert dev is not None
        assert dev.id == "agilent_bravo"
        assert dev.bbox != (0.6, 0.4)

    def test_fallback_known_sizes(self):
        dev = resolve_device("ot2")
        assert dev is not None
        assert dev.bbox == (0.62, 0.50)

    def test_unknown_device_returns_none(self):
        dev = resolve_device("totally_unknown_device_xyz")
        assert dev is None


# ---------- create_devices_from_list (向后兼容) ----------


class TestCreateDevicesFromList:
    def test_basic(self):
        specs = [{"id": "test_dev", "name": "Test"}]
        devs = create_devices_from_list(specs)
        assert len(devs) == 1
        assert devs[0].id == "test_dev"

    def test_with_explicit_size(self):
        specs = [{"id": "custom", "name": "Custom", "size": [1.0, 0.5]}]
        devs = create_devices_from_list(specs)
        assert devs[0].bbox == (1.0, 0.5)

    def test_footprint_size_used_when_no_explicit(self):
        specs = [{"id": "agilent_bravo", "name": "Bravo"}]
        devs = create_devices_from_list(specs)
        assert devs[0].bbox != (0.6, 0.4)  # 使用 footprints 中的真实尺寸

    def test_duplicate_catalog_ids_use_suffixes_and_store_uuid(self):
        specs = [
            {"id": "opentrons_liquid_handler", "uuid": "u1"},
            {"id": "opentrons_liquid_handler", "uuid": "u2"},
        ]
        devs = create_devices_from_list(specs)
        assert [dev.id for dev in devs] == [
            "opentrons_liquid_handler",
            "opentrons_liquid_handler#2",
        ]
        assert [dev.uuid for dev in devs] == ["u1", "u2"]


# ---------- server endpoint (需要 httpx) ----------


class TestDevicesEndpoint:
    def test_get_devices(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi testclient not available")

        from ..server import app

        client = TestClient(app)
        resp = client.get("/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # 可能为空（取决于 uni-lab-assets 是否在预期路径）
        if len(data) > 0:
            first = data[0]
            assert "id" in first
            assert "bbox" in first
            assert "source" in first

    def test_filter_by_source(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi testclient not available")

        from ..server import app

        client = TestClient(app)
        resp = client.get("/devices?source=registry")
        assert resp.status_code == 200
        data = resp.json()
        for d in data:
            assert d["source"] == "registry"
