# AGENTS.md

This file provides guidance for coding agents working in this repository.

## Build & Development

```bash
# Install in editable mode (requires Python 3.12 and either ROS 2 Jazzy or Humble)
pip install -e .
uv pip install -r unilabos/utils/requirements.txt

# Run with a device graph
unilab --graph <graph.json> --config <config.py> --backend ros2
unilab --graph <graph.json> --config <config.py> --backend hostlink  # no ROS2 runtime

# Common CLI flags
unilab --test_mode                        # simulate hardware, no real execution
unilab --check_mode                       # CI validation of registry imports
unilab --skip_env_check                   # skip auto-install of dependencies
unilab --visual rviz|web|disable          # visualization mode
unilab --is_slave                         # run as slave node
unilab --role backend                     # scheduler authority and runtime.v1 control plane, without devices or ROS

# Workflow upload subcommand
unilab workflow upload -f <workflow.json> -n <name> --tags tag1 tag2

# Tests
pytest tests/                              # all tests
pytest tests/resources/test_resourcetreeset.py  # single test file
pytest tests/resources/test_resourcetreeset.py::TestClassName::test_method  # single test
```

## Architecture

### Startup Flow

`unilab` CLI → `unilabos/app/cli/parser.py:build_parser()` → `app/cli/router.py` handles lightweight subcommands (`package` included) → `unilabos/app/main.py:main()` loads config and starts device runtime only when no CLI subcommand handled the request. Runtime then builds the registry, reads the device graph (JSON/GraphML), and starts `hostlink` or `ros2`. HostLink's local driver executor is `unilabos.backend.hostlink.local_runtime.HostLinkLocalRuntime`.

### Core Layers

**Registry** (`unilabos/registry/`): Singleton `Registry` class discovers and catalogs all device types, resource types, and communication devices from YAML definitions. Device types live in `registry/devices/*.yaml`, resources in `registry/resources/`, comms in `registry/device_comms/`. The registry resolves class paths to actual Python classes via `utils/import_manager.py`.

**Resource Tracking** (`unilabos/resources/resource_tracker.py`): Pydantic-based `ResourceDict` → `ResourceDictInstance` → `ResourceTreeSet` hierarchy. `ResourceTreeSet` is the canonical in-memory representation of all devices and resources, used throughout the system. Graph I/O is in `resources/graphio.py` (reads JSON/GraphML device topology files into `nx.Graph` + `ResourceTreeSet`).

**Device Drivers** (`unilabos/devices/`): 30+ hardware drivers organized by device type (liquid_handling, hplc, balance, arm, etc.). Each driver is a Python class that gets wrapped by `backend/ros2/device_node_wrapper.py:ros2_device_node()` to become a ROS2 node with publishers, subscribers, and action servers.

**Device Runtime** (`unilabos/backend/runtime/`): Transport-neutral device execution kernel shared by HostLink and ROS 2 — `DeviceNode`, action routing, resource service, and runtime exceptions (`DeviceActionError`, `DeviceClassInvalid`, `ActionResultError`).

**Transport Layers**: `unilabos/backend/ros2/` wraps device classes as `ROS2DeviceNode` instances; its presets include `host_node`, `workstation`, controller, serial, and camera nodes. `unilabos/backend/hostlink/` provides the HostLink transport runtime plus its own `host_node.py:HostNode` and `workstation.py:WorkstationNode` (the ROS2 counterpart keeps its original name `ROS2WorkstationNode`); `unilabos/backend/dora/` is experimental. The per-backend host/workstation orchestrators share transport-neutral logic from `backend/runtime/` (`host_adapter.py:HostAdapterBase` for bookkeeping/bridge notification/ping-pong/test-mode, `workstation_protocol.py` for protocol name/model resolution and resource expand/write-back) and carry no `@device` decorator. `backend/host_services.py:HostServices` uniquely defines the host service actions; the file is excluded from the default registry AST scan and scanned separately by `Registry._setup_host_node`. Host-to-device material and management requests use the module-level functions in `backend/hostlink/downlink.py` (cross-machine channel is HostLink RPC; ros2 reuses it).

**Experiment Protocols** (`unilabos/experiments/`): `models.py` holds Pydantic parameter models for XDL-style experiment actions (mirroring `unilabos_msgs` ROS actions); `compile/` holds 20+ protocol compilers (add, centrifuge, dissolve, filter, heatchill, stir, pump, etc.) that expand protocol steps into device action sequences at execution time (consumed by the `workstation` preset node). One-shot workflow import converters (`from_xdl.py`, `from_python_script.py`, legacy JSON) live in `scripts/workflow/`, not in the runtime packages.

**Communication** (`unilabos/device_comms/`): Hardware communication adapters — OPC-UA client, Modbus PLC, RPC, and a universal driver. `app/communication.py` provides a factory pattern for WebSocket client connections to the cloud.

**Microbackend HTTP API** (`unilabos/server/api/`): FastAPI routers and the Host management application. It exposes runtime, materials, telemetry, history, and scheduler observation APIs on port 8002 by default.

### Configuration System

- **Config classes** in `unilabos/config/config.py`: `BasicConfig`, `WSConfig`, `HTTPConfig`, `ROSConfig` — all class-level attributes, loaded from Python config files
- Config files are `.py` files with matching class names (see `config/example_config.py`)
- Environment variables override with prefix `UNILABOS_` (e.g., `UNILABOS_BASICCONFIG_PORT=9000`)
- Device topology defined in graph files (JSON with node-link format, or GraphML)

### Key Data Flow

1. Graph file → `graphio.read_node_link_json()` → `(nx.Graph, ResourceTreeSet, resource_links)`
2. `ResourceTreeSet` + `Registry` → `initialize_device.initialize_device_from_dict()` → `ROS2DeviceNode` instances
3. Device nodes communicate via ROS2 topics/actions or HostLink
4. Backend control notices use `server/backend/legacy_adaptor/websocket.py`; complete state and commands use the microbackend HTTP APIs in `server/api/`

### Test Data

Example device graphs and experiment configs are in `unilabos/test/experiments/` (not `tests/`). Registry test fixtures in `unilabos/test/registry/`.

## Code Conventions

- Code comments and log messages in simplified Chinese
- Python 3.12+, type hints expected
- Pydantic models for data validation (`resource_tracker.py`)
- Singleton pattern via `@singleton` decorator (`utils/decorator.py`)
- Dynamic class loading via `utils/import_manager.py` — device classes resolved at runtime from registry YAML paths
- CLI argument dashes auto-converted to underscores for consistency

## Licensing

- Framework code: GPL-3.0
- Device drivers (`unilabos/devices/`): DP Technology Proprietary License — do not redistribute
