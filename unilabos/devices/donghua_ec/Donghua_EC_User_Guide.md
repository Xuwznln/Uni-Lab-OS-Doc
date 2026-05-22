# Donghua EC 用户指南（UniLab 接入版）

## 概述

- 提供两套使用方式：
  - 测试封装动作：一条指令完成“启动实验 → 实时采样写文件 → 导出数据”，可选自动停止。
  - 基础启动动作：按需组合“启动实验、实时输出、停止、导出”，更灵活可编排。

## 设备配置

- `interface_dir`：DHInterface 目录（包含 `ECCore.dll` 与配置文件），示例：`d:\Uni-Lab-OS\Uni-Lab-OS\unilabos\devices\donghua_ec\x64release\DHInterface`（注册见 `unilabos/registry/devices/donghua_ec.yaml:1940`）。
- `dll_path`（可选）：若与 `interface_dir` 不一致，可直接指定 `ECCore.dll` 完整路径（`donghua_ec.yaml:1936`）。
- 默认通道：`machine_id`（`donghua_ec.yaml:1944`）。

## 初始化

- 后端自动：设备注册后会自动调用 `auto-initialize`（加载 DLL）与 `auto-post_init`（注入），无需前端干预（`donghua_ec.yaml:27`、`donghua_ec.yaml:48`）。
- 手动（可选）：
  - 调用 `auto-initialize`：`{"device_id":"<设备ID>","action":"auto-initialize"}`
  - 调用 `auto-post_init`：`{"device_id":"<设备ID>","action":"auto-post_init"}`

## 动作总览

- 测试封装动作（均要求传入 `output_dir`）：
  - `test_open_circuit_energy`（默认 `stop_after=true`，使用轮询检测实验结束后再停止与导出，不再使用 `wait_seconds`）
  - `test_eis`（默认 `stop_after=false`，避免提前结束，`donghua_ec.yaml:1480`）
  - `test_gitt`（默认 `stop_after=false`，`donghua_ec.yaml:1627`）
  - `test_linear_scan_voltammetry`（默认 `stop_after=false`，必填 `output_dir`，参考 `donghua_ec.yaml:1750` 及后续）
- 基础启动与组合：
  - `start_open_circuit_energy`、`start_eis`、`start_gitt`、`start_linear_scan_voltammetry`
  - 实时输出：`start_realtime_output` / `stop_realtime_output`（`donghua_ec.yaml:1068`、`donghua_ec.yaml:1155`）
  - 停止实验：`stop_experiment`（`donghua_ec.yaml:1118`）
  - 导出数据：`export_*_data`（如 `export_eis_data`、`export_gitt_data` 等，均要求 `output_dir/dest_dir`）

## 快速测试流程（推荐）

- 开路电位：
  - 请求：
    ```json
    {"device_id":"<设备ID>","action":"test_open_circuit_energy","action_args":{"output_dir":"d:/data/oc","interval":0.5,"stop_after":true}}
    ```
  - 返回包含：`success`、`realtime_file`、`export_files`、`export_dest`。
- 阻抗（EIS）：
  - 请求（只需给导出目录，其他用默认即可）：
    ```json
    {"device_id":"<设备ID>","action":"test_eis","action_args":{"output_dir":"d:/data/eis","start_freq":10000,"end_freq":0.1,"amplitude":0.01,"point_count":10,"interval":0.5}}
    ```
  - 默认不自动停止（`stop_after=false`），可在完成采样后继续扫频；若需自动停，传 `stop_after=true`。
- GITT：
  - 请求：
    ```json
    {"device_id":"<设备ID>","action":"test_gitt","action_args":{"output_dir":"d:/data/gitt","current":1.0,"time_per_point_cc":0.1,"continue_time_cc":60,"time_per_point_oc":0.1,"continue_time_oc":60,"is_voltage_trig":true,"voltage_or_current_trig_direction":0,"voltage_or_current_trig_value":0,"interval":0.5}}
    ```

## 基础启动与组合（灵活编排）

- 启动 EIS：
  - `{"device_id":"<设备ID>","action":"start_eis","action_args":{"start_freq":10000,"end_freq":0.1,"amplitude":0.01,"point_count":10}}`
- 开启实时输出：
  - `{"device_id":"<设备ID>","action":"start_realtime_output","action_args":{"interval":0.5}}`
- 关闭实时输出并获取文件：
  - `{"device_id":"<设备ID>","action":"stop_realtime_output"}`
- 导出数据到目录：
  - `{"device_id":"<设备ID>","action":"export_eis_data","action_args":{"output_dir":"d:/data/eis"}}`
- 停止实验（可选）：
  - `{"device_id":"<设备ID>","action":"stop_experiment"}`

## 重要说明

- 必填导出目录：所有 `test_*` 和 `export_*` 动作需要提供 `output_dir`（或 `dest_dir`），否则不会复制数据到目标位置（`donghua_ec.yaml:1545`、`donghua_ec.yaml:1710`）。
- 关于提前结束：非开路的测试封装动作默认 `stop_after=false`，避免在实时采样后调用 `stop_experiment`，从而导致频率扫描未达到 `end_freq` 就停止（修复见 `donghua_ec.yaml:1480`、`donghua_ec.yaml:1627`）。
- 实时文件位置：若未指定 `dest_dir`，实时输出会写入 `interface_dir/SourceData/<日期>/<实验子目录>`（实现参考 `unilabos/devices/donghua_ec/donghua_ec.py:1042`）。

## 数据字段（参考）

- EIS 拆分：`time/zre/zim/z/freq/phase/edc`（实现参考 `unilabos/devices/donghua_ec/donghua_ec.py:1109`）。
- 线性扫描与循环伏安：`time/potential/current` 等（实现参考 `donghua_ec.py:1111`、`donghua_ec.py:1114`）。
- 开路电位：写入时间序列与电位（`donghua_ec.py:1045`）。
