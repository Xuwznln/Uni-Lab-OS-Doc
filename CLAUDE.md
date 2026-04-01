# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
# Install (requires mamba env with python 3.11)
mamba create -n unilab python=3.11.14
mamba activate unilab
mamba install uni-lab::unilabos-env -c robostack-staging -c conda-forge
pip install -e .
uv pip install -r unilabos/utils/requirements.txt

# Run with a device graph
unilab --graph <graph.json> --config <config.py> --backend ros
unilab --graph <graph.json> --config <config.py> --backend simple  # no ROS2 needed

# Common CLI flags
unilab --app_bridges websocket fastapi    # communication bridges
unilab --test_mode                        # simulate hardware, no real execution
unilab --check_mode                       # CI validation of registry imports (AST-based)
unilab --skip_env_check                   # skip auto-install of dependencies
unilab --visual rviz|web|disable          # visualization mode
unilab --is_slave                         # run as slave node
unilab --restart_mode                     # auto-restart on config changes (supervisor/child process)
unilab --external_devices_only            # only load external device packages
unilab --extra_resource                   # load extra lab_ prefixed labware resources

# Workflow upload subcommand
unilab workflow_upload -f <workflow.json> -n <name> --tags tag1 tag2

# Labware Manager (standalone web UI for PRCXI labware CRUD, port 8010)
python -m unilabos.labware_manager

# Tests
pytest tests/                              # all tests
pytest tests/resources/test_resourcetreeset.py  # single test file
pytest tests/resources/test_resourcetreeset.py::TestClassName::test_method  # single test

# CI check (matches .github/workflows/ci-check.yml)
python -m unilabos --check_mode --skip_env_check

# If registry YAML/Python files changed, regenerate before committing:
python -m unilabos --complete_registry

# Documentation build
cd docs && python -m sphinx -b html . _build/html -v
```

## Architecture

### Startup Flow

`unilab` CLI (entry point in `setup.py`) → `unilabos/app/main.py:main()` → loads config → builds registry → reads device graph (JSON/GraphML) → starts backend thread (ROS2/simple) → starts FastAPI web server + WebSocket client.

### Core Layers

**Registry** (`unilabos/registry/`): Singleton `Registry` class discovers and catalogs all device types, resource types, and communication devices. Two registration mechanisms:
1. **YAML definitions** in `registry/devices/*.yaml` and `registry/resources/` (backward-compatible)
2. **Python decorators** (`@device`, `@action`, `@resource` in `registry/decorators.py`) — preferred for new code

AST scanning (`ast_registry_scanner.py`) discovers decorated classes without importing them, so `--check_mode` works without hardware dependencies. Class paths resolved to Python classes at runtime via `utils/import_manager.py`.

Decorator usage pattern:
```python
from unilabos.registry.decorators import device, action, resource
from unilabos.registry.decorators import InputHandle, OutputHandle, HardwareInterface

@device(id="my_device.v1", category=["category_name"], handles=[...])
class MyDevice:
    @action(action_type=SomeActionType)
    def do_something(self): ...
```

**Resource Tracking** (`unilabos/resources/resource_tracker.py`): Pydantic-based `ResourceDict` → `ResourceDictInstance` → `ResourceTreeSet` hierarchy. `ResourceTreeSet` is the canonical in-memory representation of all devices and resources. Graph I/O in `resources/graphio.py` reads JSON/GraphML device topology files into `nx.Graph` + `ResourceTreeSet`.

**Device Drivers** (`unilabos/devices/`): 30+ hardware drivers organized by category (liquid_handling, hplc, balance, arm, etc.). Each driver class gets wrapped by `ros/device_node_wrapper.py:ros2_device_node()` into a `ROS2DeviceNode` (defined in `ros/nodes/base_device_node.py`) with publishers, subscribers, and action servers.

**ROS2 Layer** (`unilabos/ros/`): Preset node types in `ros/nodes/presets/` — `host_node` (main orchestrator, ~90KB), `controller_node`, `workstation`, `serial_node`, `camera`, `resource_mesh_manager`. Custom messages in `unilabos_msgs/` (80+ action types, pre-built via conda `ros-humble-unilabos-msgs`).

**Protocol Compilation** (`unilabos/compile/`): 20+ protocol compilers (add, centrifuge, dissolve, filter, heatchill, stir, pump, etc.) registered in `__init__.py:action_protocol_generators` dict. Utility parsers in `compile/utils/` (vessel, unit, logger).

**Workflow** (`unilabos/workflow/`): Converts workflow definitions from multiple formats — JSON (`convert_from_json.py`, `common.py`), Python scripts (`from_python_script.py`), XDL (`from_xdl.py`) — into executable `WorkflowGraph`. Legacy converters in `workflow/legacy/`.

**Communication** (`unilabos/device_comms/`): Hardware adapters — OPC-UA, Modbus PLC, RPC, universal driver. `app/communication.py` provides factory pattern for WebSocket connections.

**Web/API** (`unilabos/app/web/`): FastAPI server with REST API (`api.py`), Jinja2 templates (`pages.py`), HTTP client (`client.py`). Default port 8002.

**Labware Manager** (`unilabos/labware_manager/`): Standalone FastAPI web app (port 8010) for PRCXI labware CRUD. Pydantic models in `models.py`, JSON database in `labware_db.json`. Supports importing from existing Python/YAML (`importer.py`), code generation (`codegen.py`), and YAML generation (`yaml_gen.py`). Web UI with SVG visualization (`static/labware_viz.js`), dynamic form handling (`static/form_handler.js`), and Jinja2 templates.

### Configuration System

- **Config classes** in `unilabos/config/config.py`: `BasicConfig`, `WSConfig`, `HTTPConfig`, `ROSConfig` — class-level attributes, loaded from Python `.py` config files (see `config/example_config.py`)
- Environment variable overrides with prefix `UNILABOS_` (e.g., `UNILABOS_BASICCONFIG_PORT=9000`)
- Device topology defined in graph files (JSON node-link format or GraphML)

### Key Data Flow

1. Graph file → `graphio.read_node_link_json()` → `(nx.Graph, ResourceTreeSet, resource_links)`
2. `ResourceTreeSet` + `Registry` → `initialize_device.initialize_device_from_dict()` → `ROS2DeviceNode` instances
3. Device nodes communicate via ROS2 topics/actions or direct Python calls (simple backend)
4. Cloud sync via WebSocket (`app/ws_client.py`) and HTTP (`app/web/client.py`)

### Test Data

Example device graphs and experiment configs are in `unilabos/test/experiments/` (not `tests/`). Registry test fixtures in `unilabos/test/registry/`.

## Code Conventions

- Code comments and log messages in **simplified Chinese**
- Python 3.11+, type hints expected
- Pydantic models for data validation (`resource_tracker.py`)
- Singleton pattern via `@singleton` decorator (`utils/decorator.py`)
- Dynamic class loading via `utils/import_manager.py` — device classes resolved at runtime from registry YAML paths
- CLI argument dashes auto-converted to underscores for consistency
- No linter/formatter configuration enforced (no ruff, black, flake8, mypy configs)
- Documentation built with Sphinx (Chinese language, `sphinx_rtd_theme`, `myst_parser`)
- CI runs on Windows (`windows-latest`); if registry files change, run `python -m unilabos --complete_registry` locally before committing

## Licensing

- Framework code: GPL-3.0
- Device drivers (`unilabos/devices/`): DP Technology Proprietary License — do not redistribute
