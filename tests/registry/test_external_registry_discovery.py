"""Plan 09 Task 1: external registry path discovery."""

from pathlib import Path

import unilabos.registry.external_registry_discovery as discovery
from unilabos.registry.external_registry_discovery import (
    discover_registry_paths_from_entry_points,
    discover_registry_paths_from_project,
)


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


def test_discover_from_entry_points(monkeypatch, tmp_path):
    reg = tmp_path / "unilabos_registry"
    reg.mkdir()

    class _FakeEP:
        name = "my_package"

        def load(self):
            return lambda: [str(reg)]

    monkeypatch.setattr(discovery, "entry_points", lambda group=None: [_FakeEP()])

    paths = discover_registry_paths_from_entry_points()

    assert paths == [reg.resolve()]


def test_discover_from_entry_points_isolates_bad_entry(monkeypatch):
    class _BadEP:
        name = "broken"

        def load(self):
            raise ImportError("boom")

    monkeypatch.setattr(discovery, "entry_points", lambda group=None: [_BadEP()])

    assert discover_registry_paths_from_entry_points() == []
