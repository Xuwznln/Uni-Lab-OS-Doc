"""Plan 09 Task 5: external variant fixture registry path is discoverable (locks the
fixture before it is wired into startup via build_registry/setup)."""

from pathlib import Path

from unilabos.registry.external_registry_discovery import discover_registry_paths_from_project


def test_external_variant_fixture_registry_path_is_discoverable():
    project_root = Path(__file__).parent / "fixtures" / "external_variant_package"

    paths = discover_registry_paths_from_project(project_root)

    assert paths == [(project_root / "unilabos_registry").resolve()]
