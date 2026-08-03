"""社区设备包目录式注册表发现合同。"""

from pathlib import Path

from unilabos.app.package_cli import discover_registry_paths_from_project


def test_discover_registry_paths_from_pyproject_tool_section():
    project_root = Path(__file__).parent / "fixtures" / "external_variant_package"

    paths = discover_registry_paths_from_project(project_root)

    assert paths == [(project_root / "unilabos_registry").resolve()]


def test_discover_registry_paths_falls_back_to_unilabos_registry_directory(tmp_path):
    registry_dir = tmp_path / "unilabos_registry"
    registry_dir.mkdir()

    paths = discover_registry_paths_from_project(tmp_path)

    assert paths == [registry_dir.resolve()]


def test_discover_registry_paths_returns_empty_when_no_registry_exists(tmp_path):
    paths = discover_registry_paths_from_project(tmp_path)

    assert paths == []
