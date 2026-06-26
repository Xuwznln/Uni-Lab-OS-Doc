---
name: upload-external-package
description: Upload an external Uni-Lab-OS device package to Device Square, including YAML registry packages, AST @device packages, optional 3D model upload, model path patching, and real-vs-virtual device distinctions. Use when asked to upload external packages, community packages, registry.yaml packages, package models, or Device Square templates.
---

# Upload External Package To Device Square

## Core Workflow

- Use the active `LeapLab/Uni-Lab-OS` checkout for `unilabos.app.package_cli`; avoid stale installed CLI code.
- Use the package's existing `pyproject.toml` if present. Create a minimal one only when absent.
- Determine registry source before editing:
  - AST style: device classes already use `@device`; do not construct YAML for those devices.
  - YAML style: root `registry.yaml` defines devices; use this for unchanged external drivers or plain classes.
  - Mixed packages are allowed only if device IDs do not duplicate between AST and YAML.
- Validate locally with `inspect_package`, then upload with AK/SK; never write secrets into package files or scratch JSON.

## YAML Device Conventions

- Use a root mapping keyed by stable ASCII `device_id`.
- Each device should include:
  - `version`, `resource_type: device`
  - `category`, `displayname`, `description` (Chinese-facing metadata when publishing to square)
  - `model` if a 3D model exists
  - `init_param_schema`
  - `class.module`, `class.type: python`, `class.init`, `class.action_value_mappings`, `class.status_types`
- Keep external/raw drivers unchanged; point `class.module` at real importable classes.
- Put every `${config.*}` placeholder used in `class.init` into `init_param_schema.config.properties` and `required`.
- Expose real parameterized actions in `class.action_value_mappings`; do not ship setup-only devices.
- Prefer public hardware operations; skip private/internal helpers and object-rich parameters unless the caller can provide resolvable resource/device IDs.
- For one class with many models, create multiple YAML entries sharing the class but differing metadata, model, and init/backend config.
- For real devices, avoid `mock`, `virtual`, `chatterbox`, and `simulator` backends or descriptions.

## Upload Without Models

- Run `inspect_package(<package_path>)` with `PYTHONPATH=<LeapLab/Uni-Lab-OS>[:<package_path>]`.
- Confirm:
  - source is expected (`registry.yaml` or AST scan)
  - resource count and device IDs are correct
  - `source_registry` exists
  - display names, descriptions, config schema, and action schemas are present
- Upload the package to the target addr, then save only sanitized response summaries.

## Upload With Models

- Expected local model layout: `device_models_zup/<mesh>/modal.xacro` plus `meshes/*`.
- Device model block:

```yaml
model:
  type: device
  format: xacro
  mesh: <mesh_folder>
  mesh_tf: [0, 0, 0, 0, 0, 0]
  encrypted: true
  path: ""
```

- Sequence:
  - Upload package once so Device Square templates exist.
  - Resolve each `template_uuid` from `/lab/square/list` or equivalent template search.
  - Upload models with `unilabos.app.model_upload.upload_device_model(...)`.
  - Let the helper handle encryption: `modal.xacro` stays plaintext; mesh files are XOR-encrypted; publish uses `encrypted=True`.
  - Patch the returned `model.path` back into the source registry:
    - YAML package: patch `registry.yaml`.
    - AST package: patch the `@device(model=...)` or `id_meta[id]["model"]` metadata.
  - Re-upload the package so `source_registry` contains final model paths.

## Real Vs Virtual Devices

- Real device entries should construct real drivers/backends and should not silently fall back to simulation.
- Virtual devices should be separate entries/packages with virtual runtime metadata and virtual backends.
- Model metadata belongs to the device entry, not the backend.
- Real/virtual pair metadata does not automatically carry models; each renderable real or virtual entry needs its own `model` block.

## Final Checks

- Existing `pyproject.toml` preserved unless user asked to change it.
- No duplicate device IDs across YAML and AST sources.
- `inspect_package` reports expected resource count.
- Real devices have no accidental virtual/mock/chatterbox/simulator references.
- Actions are present beyond `setup`/`stop`.
- Model paths are nonblank when models are required.
- `model.encrypted: true` appears in registry and generated resources when encrypted helper upload was used.
- Final upload returns success, and saved logs/responses do not contain AK/SK.
