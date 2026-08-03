"""外部变体 fixture 可由包管理公共入口发现。"""

from pathlib import Path

from unilabos.app.package_cli import discover_registry_paths_from_project


def test_external_variant_fixture_registry_path_is_discoverable():
    project_root = Path(__file__).parent / "fixtures" / "external_variant_package"

    paths = discover_registry_paths_from_project(project_root)

    assert paths == [(project_root / "unilabos_registry").resolve()]
