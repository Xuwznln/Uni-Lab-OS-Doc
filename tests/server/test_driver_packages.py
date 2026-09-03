"""驱动包台账 / API：不真正调用 pip，桩掉子进程只验证台账与操作状态机。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from unilabos.server.api.driver_packages import create_driver_packages_router
from unilabos.server.services import driver_packages as dp


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dp.DriverPackageService:
    svc = dp.DriverPackageService(tmp_path)
    svc.configure(scan_dirs=[str(tmp_path / "site_demo")], external_only=True)
    monkeypatch.setattr(dp, "_service", svc)
    return svc


def _wait(svc: dp.DriverPackageService, operation_id: str) -> dict:
    for _ in range(200):
        record = svc.operation(operation_id)
        if record["status"] != "running":
            return record
        time.sleep(0.01)
    raise AssertionError("operation did not finish")


def test_ledger_roundtrip_and_enabled_dirs(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkg_a"
    pkg_dir.mkdir()
    records = {
        "pkg_a": dp.DriverPackageRecord(name="pkg-a", package_dirs=[str(pkg_dir)], device_ids=["dev_a"], enabled=True),
        "pkg_b": dp.DriverPackageRecord(name="pkg-b", package_dirs=[str(tmp_path / "missing")], enabled=True),
        "pkg_c": dp.DriverPackageRecord(name="pkg-c", package_dirs=[str(pkg_dir)], enabled=False),
    }
    dp.save_ledger(tmp_path, records)
    loaded = dp.load_ledger(tmp_path)
    assert set(loaded) == {"pkg_a", "pkg_b", "pkg_c"}
    assert loaded["pkg_a"].device_ids == ["dev_a"]
    # 缺失目录与未启用的包不参与挂载
    assert dp.enabled_package_dirs(tmp_path) == [str(pkg_dir)]


def test_install_operation_records_ledger(service: dp.DriverPackageService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg_dir = tmp_path / "acme_devices"
    pkg_dir.mkdir()
    snapshots = iter([{"pip": "1"}, {"pip": "1", "acme_devices": "0.3.0"}])
    monkeypatch.setattr(dp, "_distribution_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(dp, "_dist_package_dirs", lambda name: [str(pkg_dir)])
    monkeypatch.setattr(
        "unilabos.app.cli.package._installed_device_ids", lambda name: ["acme_pump", "acme_valve"]
    )

    calls: list[list[str]] = []

    def fake_pip(operation_id: str, args: list[str]) -> int:
        calls.append(args)
        service._append_log(operation_id, "Successfully installed acme-devices-0.3.0")
        return 0

    monkeypatch.setattr(service, "_run_pip", fake_pip)

    started = service.start_install("git+https://example.com/acme/devices.git", enable=True)
    assert started["status"] == "running"
    done = _wait(service, started["operation_id"])
    assert done["status"] == "succeeded", done
    assert calls == [["install", "git+https://example.com/acme/devices.git"]]
    assert done["result"]["restart_required"] is True
    assert done["result"]["packages"][0]["device_ids"] == ["acme_pump", "acme_valve"]

    inventory = service.inventory()
    assert inventory["restart_required"] is True
    (package,) = inventory["packages"]
    assert package["name"] == "acme_devices"
    assert package["version"] == "0.3.0"
    assert package["enabled"] is True
    assert package["mounted"] is False  # 还没重启，不在当前扫描目录里
    assert dp.enabled_package_dirs(tmp_path) == [str(pkg_dir)]


def test_reinstall_same_version_by_local_path(
    service: dp.DriverPackageService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地目录重复安装（版本不变）也要照常登记：Windows 绝对路径不能被当成叫 ``C`` 的包。"""
    src = tmp_path / "acme_src"
    src.mkdir()
    (src / "pyproject.toml").write_text('[project]\nname = "acme-devices"\nversion = "0.1.0"\n', encoding="utf-8")
    spec = str(src)
    assert dp._spec_looks_like_path(spec) is True
    assert dp._spec_looks_like_path("acme-devices==0.1.0") is False
    assert dp._spec_distribution_name(spec) == "acme-devices"
    assert dp._spec_distribution_name("acme-devices==0.1.0") == "acme-devices"
    assert dp._spec_distribution_name("git+https://example.com/x.git") == ""

    monkeypatch.setattr(dp, "_distribution_snapshot", lambda: {"acme_devices": "0.1.0"})
    monkeypatch.setattr(dp, "_dist_package_dirs", lambda name: [str(src)])
    monkeypatch.setattr("unilabos.app.cli.package._installed_device_ids", lambda name: ["acme_pump"])
    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_run_pip", lambda operation_id, args: calls.append(args) or 0)

    done = _wait(service, service.start_install(spec, upgrade=True)["operation_id"])
    assert done["status"] == "succeeded", done
    assert calls == [["install", "--upgrade", spec]]
    assert service.inventory()["packages"][0]["device_ids"] == ["acme_pump"]


def test_install_git_spec_with_name_hint(service: dp.DriverPackageService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """索引条目带 name：git 规格重装同版本（环境无差异）也能按该名字登记。"""
    pkg_dir = tmp_path / "site_demo"
    pkg_dir.mkdir()
    monkeypatch.setattr(dp, "_distribution_snapshot", lambda: {"site_demo": "0.2.0"})
    monkeypatch.setattr(dp, "_dist_package_dirs", lambda name: [str(pkg_dir)])
    monkeypatch.setattr("unilabos.app.cli.package._installed_device_ids", lambda name: ["material_bench_demo"])
    monkeypatch.setattr(service, "_run_pip", lambda operation_id, args: 0)

    spec = "git+https://github.com/Xuwznln/LabDeviceSiteDemo.git"
    started = service.start_install(spec, name="site_demo")
    assert started["package_name"] == "site_demo"
    done = _wait(service, started["operation_id"])
    assert done["status"] == "succeeded", done
    (package,) = service.inventory()["packages"]
    assert package["name"] == "site_demo" and package["spec"] == spec
    assert package["device_ids"] == ["material_bench_demo"]

    # 名字提示不在环境里 → 退回差异识别；这里没有差异，明确失败并留下提示
    done = _wait(service, service.start_install(spec, name="ghost-pkg")["operation_id"])
    assert done["status"] == "failed" and "ghost-pkg" in done["log"]
    with pytest.raises(dp.DriverPackageError):
        service.start_install(spec, name="bad name!")


def test_catalog_merges_remote_and_local(service: dp.DriverPackageService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dp.save_ledger(tmp_path, {"acme_devices": dp.DriverPackageRecord(name="acme-devices", version="0.1.0")})
    # 没配索引地址：只有本地目录，且文件可选
    monkeypatch.setattr(dp.DriverPackageService, "_index_url", staticmethod(lambda: ""))
    empty = service.catalog()
    assert empty["packages"] == []
    assert empty["sources"] == [{"kind": "local", "location": str(dp.catalog_path(tmp_path)), "ok": True, "count": 0, "missing": True}]

    dp.catalog_path(tmp_path).write_text(
        '{"packages": [{"name": "lab-plates", "spec": "git+https://lab.example/plates.git", "devices": ["plate_reader"]},'
        ' {"name": "acme-devices", "spec": "acme-devices", "version": "9.9"}, {"name": ""}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(dp.DriverPackageService, "_index_url", staticmethod(lambda: "https://index.example/packages.json"))
    monkeypatch.setattr(
        dp.DriverPackageService,
        "_fetch_json",
        staticmethod(lambda url: [{"name": "acme-devices", "spec": "acme-devices==0.2.0", "tags": ["pump"]}]),
    )
    merged = service.catalog()
    assert [item["kind"] for item in merged["sources"]] == ["remote", "local"]
    assert merged["sources"][0]["count"] == 1 and merged["sources"][1]["count"] == 1  # 同名以官方索引为准
    by_name = {item["name"]: item for item in merged["packages"]}
    assert by_name["acme-devices"]["spec"] == "acme-devices==0.2.0"
    assert by_name["acme-devices"]["official"] is True and by_name["acme-devices"]["installed"] is True
    assert by_name["lab-plates"]["official"] is False and by_name["lab-plates"]["installed"] is False
    assert by_name["lab-plates"]["devices"] == ["plate_reader"]

    def _boom(url: str) -> None:
        raise OSError("offline")

    monkeypatch.setattr(dp.DriverPackageService, "_fetch_json", staticmethod(_boom))
    degraded = service.catalog()
    assert degraded["sources"][0]["ok"] is False and "offline" in degraded["sources"][0]["error"]
    assert [item["name"] for item in degraded["packages"]] == ["lab-plates", "acme-devices"]


def test_install_failure_and_toggle(service: dp.DriverPackageService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dp, "_distribution_snapshot", lambda: {})
    monkeypatch.setattr(service, "_run_pip", lambda operation_id, args: 1)
    done = _wait(service, service.start_install("nope==9.9")["operation_id"])
    assert done["status"] == "failed"
    assert "退出码 1" in done["error"]

    dp.save_ledger(service.working_dir, {"x": dp.DriverPackageRecord(name="x", package_dirs=[], enabled=True)})
    record = service.set_enabled("x", False)
    assert record["enabled"] is False
    with pytest.raises(dp.DriverPackageError):
        service.set_enabled("missing", True)


def test_router_endpoints(service: dp.DriverPackageService, monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(create_driver_packages_router())
    client = TestClient(app)

    response = client.get("/api/v1/driver-packages")
    assert response.status_code == 200
    body = response.json()
    assert body["scan_dirs"] and body["external_only"] is True
    assert body["packages"] == []
    monkeypatch.setattr(dp.DriverPackageService, "_index_url", staticmethod(lambda: ""))
    catalog = client.get("/api/v1/driver-packages/catalog")
    assert catalog.status_code == 200 and catalog.json()["packages"] == []

    assert client.post("/api/v1/driver-packages/install", json={"spec": ""}).status_code == 422
    assert client.get("/api/v1/driver-packages/operations/unknown").status_code == 404
    assert client.put("/api/v1/driver-packages/ghost/enabled", json={"enabled": True}).status_code == 404

    monkeypatch.setattr(dp, "_distribution_snapshot", lambda: {})
    monkeypatch.setattr(service, "_run_pip", lambda operation_id, args: 0)
    accepted = client.post("/api/v1/driver-packages/install", json={"spec": "acme-devices"})
    assert accepted.status_code == 202
    operation_id = accepted.json()["operation_id"]
    done = _wait(service, operation_id)
    assert done["status"] == "failed"  # 桩：装完没有新分发
    listed = client.get("/api/v1/driver-packages/operations").json()
    assert listed[0]["operation_id"] == operation_id
