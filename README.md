<div align="center">
  <img src="docs/logo.png" alt="Uni-Lab Logo" width="200"/>
</div>

# Uni-Lab-OS

<!-- Language switcher -->

**English** | [中文](README_zh.md)

[![GitHub Stars](https://img.shields.io/github/stars/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/network/members)
[![GitHub Issues](https://img.shields.io/github/issues/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/issues)
[![GitHub License](https://img.shields.io/github/license/deepmodeling/Uni-Lab-OS.svg)](https://github.com/deepmodeling/Uni-Lab-OS/blob/main/LICENSE)

Uni-Lab-OS is a platform for laboratory automation, designed to connect and control various experimental equipment, enabling automation and standardization of experimental workflows.

## Key Features

- Multi-device integration management
- Automated experimental workflows
- Cloud connectivity capabilities
- Flexible configuration system
- Support for multiple experimental protocols

## Documentation

Detailed documentation can be found at:

- [Online Documentation](https://deepmodeling.github.io/Uni-Lab-OS/)

## Supported Runtime

The current binary and development baseline is **Python 3.12.13 (`cp312`) + NumPy
2**. ROS 2 Jazzy is the default (`robostack-jazzy`, mutex `0.15.*`), and ROS 2
Humble is also supported (`robostack-humble`, mutex `0.9.*`). Use separate Conda
environments and never mix the two RoboStack channels. See the
[runtime and ABI baseline](docs/user_guide/runtime_baseline.md) for migration and
verification instructions.

## Quick Start

### 1. Setup Conda Environment

Uni-Lab-OS recommends using `mamba` for environment management. Choose the package that fits your needs:

| Package | Use Case | Contents |
|---------|----------|----------|
| `unilabos` | **Recommended for most users** | Complete package, ready to use |
| `unilabos-env` | Developers (editable install) | Environment only, install unilabos via pip |
| `unilabos-full` | Simulation/Visualization | unilabos + ROS2 Desktop + Gazebo + MoveIt |

```bash
# Create new environment
mamba create -n unilab python=3.12.13
mamba activate unilab

# Option A: Standard installation (recommended for most users)
mamba install uni-lab::unilabos -c uni-lab -c conda-forge -c robostack-jazzy

# Option B: For developers (editable mode development)
mamba install uni-lab::unilabos-env -c uni-lab -c conda-forge -c robostack-jazzy
# Then install unilabos and dependencies:
git clone https://github.com/deepmodeling/Uni-Lab-OS.git && cd Uni-Lab-OS
pip install -e .
uv pip install -r unilabos/utils/requirements.txt

# Option C: Full installation (simulation/visualization)
mamba install uni-lab::unilabos-full -c uni-lab -c conda-forge -c robostack-jazzy
```

For ROS 2 Humble, create a separate environment and replace every
`-c robostack-jazzy` above with `-c robostack-humble`; Conda will select the
matching `humble_1` package build automatically.

**When to use which?**
- **unilabos**: Standard installation for production deployment and general usage (recommended)
- **unilabos-env**: For developers who need `pip install -e .` editable mode, modify source code
- **unilabos-full**: For simulation (Gazebo), visualization (rviz2), and Jupyter notebooks

### 2. Clone Repository (Optional, for developers)

```bash
# Clone the repository (only needed for development or examples)
git clone https://github.com/deepmodeling/Uni-Lab-OS.git
cd Uni-Lab-OS
```

### 3. Start Uni-Lab

An Edge process owns a device graph and exposes two separate local endpoints:
the management/HTTP API (default `8002`) and the HostLink TCP channel (default
`7302`). The default `hostlink` backend does not require DDS or a ROS 2 daemon;
the `ros2` backend runs device actions and topics through ROS 2.

```bash
# HostLink runtime (the default)
unilab -g path/to/graph.json --backend hostlink --port 8002 --hostlink-port 7302

# ROS 2 Jazzy runtime (activate the Conda environment containing Jazzy first)
mamba activate unilab
unilab -g path/to/graph.json --backend ros2 --port 8002

# Validate/import the registry without starting devices
unilab --check-mode --complete-registry --skip-env-check
```

For a split deployment, start the scheduler/workflow authority separately and
point one or more Edge processes at it:

```bash
# Terminal 1: Backend authority (no device graph or device executor)
unilab --role backend --port 8081

# Terminal 2: Edge execution process
unilab -g path/to/graph.json --backend hostlink \
  --address http://127.0.0.1:8081 --port 8002
```

The Edge can also start with an empty graph (omit `-g`) when devices will be
added through the driver-package or managed-device APIs. A Slave still needs
its own graph and connects with `--is-slave --host-node-ip <host>`.

### 4. Connect to an existing or legacy Backend

Use one explicit `--address` for an existing cloud or on-premise Backend. The
value may be the service root or its `/api/v1` root; `--ak` and `--sk` provide
the laboratory credentials:

```bash
unilab -g path/to/graph.json --backend hostlink \
  --address https://legacy.example.com/api/v1 \
  --ak "$AK" --sk "$SK"
```

At startup Uni-Lab probes the HTTP routes and selects `runtime.v1` or the
legacy adapter. To pin an older Backend explicitly, put this in the
`local_config.py` loaded with `--config` (or placed in the working directory):

```python
class HTTPConfig:
    remote_addr = "https://legacy.example.com/api/v1"
    backend_protocol = "legacy"
```

Do not start `--role backend` for an old cloud Backend; that role starts the
new local `runtime.v1` authority. In both modes the browser-facing API is HTTP.
The new `control.v1` Edge↔Backend WebSocket carries short notifications and
the complete authoritative content is fetched over HTTP; the legacy adapter
keeps the old message families working.

### 5. Use a custom UI

Uni-Lab is backend-only: an Edge process does not host a SPA. Build or deploy
your UI separately (OpenLab is one example), and configure its API base URL to
the management endpoint:

| Deployment | UI API base URL |
|------------|-----------------|
| Local single-process Edge | `http://127.0.0.1:8002` |
| Split deployment | `http://127.0.0.1:8081` (the Backend authority) |
| Existing/legacy Backend | the address passed to `--address` |

Use the generated contract at `/api/openapi.json` and the interactive docs at
`/api/docs`. A Vite-style static UI can be developed and served independently:

```bash
pnpm install
pnpm dev --host 0.0.0.0       # development server
pnpm build
python -m http.server 4173 --directory dist  # simple local preview
```

The exact environment-variable name for the API base is framework-specific;
set it to one of the URLs above and use `/api/v1/...` for typed HTTP calls.
For live invalidation, a browser UI may use SSE/EventSource and then refetch
the authoritative record over HTTP. That browser notification channel is
separate from the Backend↔Edge `control.v1` WebSocket. Do not point a browser
UI at the HostLink TCP port `7302`.

External device packages can be installed from an exact Git revision and are
mounted on the next process restart:

```bash
unilab package install \
  "git+https://github.com/<org>/<device-package>.git@<commit-sha>"
```

### 6. Best Practice

See [Best Practice Guide](https://deepmodeling.github.io/Uni-Lab-OS/user_guide/best_practice.html)

## Reference Driver Implementations

Six runnable example device packages are maintained as standalone GitHub repositories (generated
from [LabDeviceTemplate](https://github.com/Xuwznln/LabDeviceTemplate)). Clone any of them, load it
with `--devices <pkg> --external_devices_only`, and read it when writing your own drivers:

| Example repository | Demonstrates |
|--------------------|--------------|
| [LabDeviceLanDemo](https://github.com/Xuwznln/LabDeviceLanDemo) | Cross-device `@subscribe` + remote `call_device_action` LAN closed loop (hub/sub as two processes) |
| [LabDeviceWorkstationDemo](https://github.com/Xuwznln/LabDeviceWorkstationDemo) | `hardware_interface` proxy — multiple sub-devices share one communication endpoint: shared serial (default IO method names) and Modbus `extra_info` (per-device `slave_id` injection) |
| [LabDeviceExceptionDemo](https://github.com/Xuwznln/LabDeviceExceptionDemo) | Exception propagation driven entirely through web-style workflow submission (`/api/v1/workflow-tasks` + `/api/v1/error-decisions`): an exception escaping the action boundary is held for an `abort` / `operator_intervention` decision, a point-to-point `call_device_action` error is caught on the caller side as a workflow node, business-level guarded returns, and an operator-replaced result letting the task finish `succeeded` |
| [LabDeviceSiteDemo](https://github.com/Xuwznln/LabDeviceSiteDemo) | Host/slave dual process — `@device(available_sites=...)` fixed sites (declaration → registry template → authoritative site instances → occupancy), `@resource` labware with the `materials.*` CRUD facade across HostLink, and `SiteSlot` action parameters (frontend Site picker uuid or label shorthand) |
| [LabDeviceLockDemo](https://github.com/Xuwznln/LabDeviceLockDemo) | Scheduler lock semantics made observable through concurrently submitted workflows: the `(device, action)` action lock serializes two `occupy` calls in submission order (the second shows up `waiting` with `blockers` in `/api/v1/scheduler/resources`), `@action(always_free=True)` lets two `peek` calls of the same action overlap, and `materials_need_lock=["plate"]` locks per authoritative plate uuid (two devices on one plate serialize, one device on two plates runs in parallel); a `lock_auditor` node reads the probes' ledgers and fails the task if any conclusion does not hold |
| [LabDeviceInventoryDemo](https://github.com/Xuwznln/LabDeviceInventoryDemo) | Quantity-based inventory through workflows: a registry `@resource` reagent template, `restock` as the web's `POST /api/v1/materials/lots/inbound` (100 ml into a fixed lot), a `dispense` step whose `inventory=[...]` requirement is reserved all-or-nothing at task start and deducted right before the action (device reports the lot after deduction, `60 / 60 / 0`), and a 500 ml requirement refused at reservation time — task `failed` / `plan_not_executable` ("short by 440 ml"), node `canceled`, device never called, lot unchanged |

Each repository README ships a step-by-step launch tutorial with verified output, and every package
carries a terminating dual-runtime smoke (`python -m <pkg>.smoke --backend hostlink|ros2`) that its
own CI runs against a pinned Uni-Lab-OS revision. For the underlying communication-sharing mechanism
see [Best Practice Guide §11.5](https://deepmodeling.github.io/Uni-Lab-OS/user_guide/best_practice.html);
to write a new driver from scratch see [Add Device](https://deepmodeling.github.io/Uni-Lab-OS/developer_guide/add_device.html).
The main repository pins all six packages in `tests/e2e/readme_demos.py` and drives each of them end
to end in CI (`tests/e2e`): registry `--check_mode`, `unilab graph create`, a real `unilab -g` runtime
with the microbackend (host plus slave where the demo is dual-process), Graph Authority round trips
through `unilab graph list/download/upload`, and the reported `@workflow` templates executed through
the management HTTP API. It also runs the LAN demo shape as separate HostLink Host/Slave processes
over loopback and an available non-loopback LAN IPv4, covering cross-device `@subscribe` followed by
a remote device action.

## Message Format

Uni-Lab-OS uses pre-built `unilabos_msgs` for system communication. You can find the built versions on the [GitHub Releases](https://github.com/deepmodeling/Uni-Lab-OS/releases) page.

## Citation

If you use [Uni-Lab-OS](https://arxiv.org/abs/2512.21766) in academic research, please cite:

```bibtex
@article{gao2025unilabos,
    title = {UniLabOS: An AI-Native Operating System for Autonomous Laboratories},
    doi = {10.48550/arXiv.2512.21766},
    publisher = {arXiv},
    author = {Gao, Jing and Chang, Junhan and Que, Haohui and Xiong, Yanfei and
              Zhang, Shixiang and Qi, Xianwei and Liu, Zhen and Wang, Jun-Jie and
              Ding, Qianjun and Li, Xinyu and Pan, Ziwei and Xie, Qiming and
              Yan, Zhuang and Yan, Junchi and Zhang, Linfeng},
    year = {2025}
}
```

## License

This project uses a dual licensing structure:

- **Main Framework**: GPL-3.0 - see [LICENSE](LICENSE)
- **Device Drivers** (`unilabos/devices/`): DP Technology Proprietary License

See [NOTICE](NOTICE) for complete licensing details.

## Project Statistics

### Stars Trend

<a href="https://star-history.com/#deepmodeling/Uni-Lab-OS&Date">
  <img src="https://api.star-history.com/svg?repos=deepmodeling/Uni-Lab-OS&type=Date" alt="Star History Chart" width="600">
</a>

## Contact Us

- GitHub Issues: [https://github.com/deepmodeling/Uni-Lab-OS/issues](https://github.com/deepmodeling/Uni-Lab-OS/issues)
