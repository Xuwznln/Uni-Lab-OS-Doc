---
name: 注册表上报白名单过滤
overview: 为 unilab 启动新增按 id 前缀过滤注册表上报的能力，使 --upload_registry 时只把瑞博小核酸站（bioyond_sirna*）和 host_node 的设备/资源报送云端，避免 YB_bottle/yb3/opentrons 等无关物料污染新实验室的物料面板。
todos:
  - id: config-attr
    content: "config.py: BasicConfig 新增 upload_registry_only = None 类属性"
    status: completed
  - id: cli-arg
    content: "main.py: 新增 --upload_registry_only (nargs='+')，解析存入 BasicConfig.upload_registry_only"
    status: completed
  - id: register-filter
    content: "register.py: register_devices_and_resources 加 id_prefixes 形参，按 id 前缀(忽略大小写)过滤设备与资源，加 INFO 日志，gather_only 分支同样处理"
    status: completed
  - id: wire-call
    content: "main.py: 调用 register_devices_and_resources 时传入 id_prefixes=BasicConfig.upload_registry_only"
    status: completed
  - id: verify
    content: 干净实验室端到端验证只剩瑞博+host_node；并回归不带参数的全量上报
    status: completed
isProject: false
---

# 注册表上报白名单过滤

## 背景与根因

启动命令带 `--upload_registry` 时，`register_devices_and_resources` 会把**整张注册表**（AST 全包扫描 + `registry/resources/**/*.yaml` 全量加载）原样报送云端，**不按 graph 过滤**。导致 `YB_bottle`/`yb3`/`YB_bottle_carriers`（属于 `BIOYOND_YB_Deck`）以及 opentrons/prcxi/laiyu 等全部被注册进新实验室的物料面板。

- 上报入口（无过滤）：`register_devices_and_resources` 遍历 `lab_registry.obtain_registry_resource_info()` / `obtain_registry_device_info()` 全量上报 —— [unilabos/app/register.py](unilabos/app/register.py)
- 调用点：`main.py` 在 `BasicConfig.upload_registry` 为真时调用 —— [unilabos/app/main.py](unilabos/app/main.py) (约 635-644 行)

## 瑞博站真正用到的 id（精确集合）

`unilabos/devices/workstation/bioyond_studio/sirna_station/` 只引用：deck `BIOYOND_SirnaStation_Deck` + 5 个物料（`sirna_station.py:148-152` → 定义于 [unilabos/resources/bioyond/sirna_materials.py](unilabos/resources/bioyond/sirna_materials.py)）。全仓库 id 以 `bioyond_sirna`（忽略大小写）开头的，正好是：

- 设备 `bioyond_sirna_station`
- deck `BIOYOND_SirnaStation_Deck`
- 物料 `bioyond_sirna_g3_200ul_tip_rack` / `bioyond_sirna_g3_50ul_tip_rack` / `bioyond_sirna_384_well_plate` / `bioyond_sirna_cell_culture_plate` / `bioyond_sirna_reagent_trough`

因此 `host_node` + `bioyond_sirna` 两个前缀即可精确命中"瑞博 + host_node"，不多不少。

## 实现方案：上报前按 id 前缀过滤（非侵入）

不改任何资源定义/扫描逻辑，只在**上报这一步**加白名单。不传新参数时行为完全不变（仍全量上报）。

### 改动点

- [unilabos/app/main.py](unilabos/app/main.py)
  - `argparse` 新增 `--upload_registry_only`（`nargs="+"`，默认 `None`），help 说明为"仅上报 id 以这些前缀开头的设备/资源（忽略大小写）"。
  - 解析后存入 `BasicConfig.upload_registry_only`（与既有 `BasicConfig.upload_registry = args_dict.get(...)` 同段，约 530 行）。
  - 调用处把前缀传入：`register_devices_and_resources(lab_registry, id_prefixes=BasicConfig.upload_registry_only)`。
- [unilabos/config/config.py](unilabos/config/config.py)
  - `BasicConfig` 新增类属性 `upload_registry_only = None`（紧邻 `upload_registry`）。
- [unilabos/app/register.py](unilabos/app/register.py)
  - `register_devices_and_resources(lab_registry, gather_only=False, id_prefixes=None)` 新增 `id_prefixes` 形参。
  - 收集 `devices_to_register` / `resources_to_register` 后，若 `id_prefixes` 非空，按 `id.lower().startswith(prefix.lower())` 过滤；记录一条 INFO 日志（过滤前/后数量 + 命中前缀）。
  - `gather_only` 分支也应用同样过滤，保持一致。

### 过滤逻辑（示意）

```python
if id_prefixes:
    prefixes = tuple(p.lower() for p in id_prefixes)

    def _match(rid: str) -> bool:
        return rid.lower().startswith(prefixes)

    devices_to_register = {k: v for k, v in devices_to_register.items() if _match(k)}
    resources_to_register = {k: v for k, v in resources_to_register.items() if _match(k)}
```

## 启动命令

带白名单过滤（只上报瑞博 + host_node）：

```bash
python -m unilabos.app.main -g _sirna_local/sirna_station_graph.example.json \
  --ak 0bbe1629-716a-4bb1-92ab-1b5c606757b4 \
  --sk d6b8cff9-d220-4d9b-bd01-76c718bb4a7e \
  --upload_registry --upload_registry_only host_node bioyond_sirna \
  --addr test --disable_browser --port 8004
```

对比：原始命令（全量上报，会带入 YB/opentrons 等多余物料）：

```bash
python -m unilabos.app.main -g _sirna_local/sirna_station_graph.example.json \
  --ak 0bbe1629-716a-4bb1-92ab-1b5c606757b4 \
  --sk d6b8cff9-d220-4d9b-bd01-76c718bb4a7e \
  --upload_registry \
  --addr test --disable_browser --port 8004
```

远程机器（参考）：

```bash
ssh -L 4303:127.0.0.1:4303 -L 44419:172.21.103.36:44419 -L 44344:127.0.0.1:44344 DP@172.21.103.36
```

## 验证

- 新建一个干净实验室（新 ak/sk 或新 lab），用上面带白名单的命令启动，确认云端物料面板只出现瑞博 5 物料 + deck，设备只有 host_node + bioyond_sirna_station。
- 不带 `--upload_registry_only` 启动一次，确认全量上报行为未变（回归）。
- 看启动日志中过滤 INFO（`[UniLab Register] 上报白名单过滤(...)` 过滤前/后数量）符合预期。

## 备注 / 风险

- 已上报到旧实验室的多余物料不会被本方案清除；按用户要求只保证新实验室正确，旧库弃用即可。
- 若云端渲染某些物料依赖通用基类资源类型（如通用 Container），白名单可能把它们也滤掉。验证时若发现物料显示异常，再把所需基类前缀补进 `--upload_registry_only`。
