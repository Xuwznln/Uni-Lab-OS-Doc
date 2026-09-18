#!/usr/bin/env bash
# 编译 layout-optimizer 目录 → 最新 RMF 运行时地图产物（building + nav_graph + world）。
# 每次启动 RMF 前调用，保证使用最新 transfers/placements。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/maps/map.env"

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER LD_LIBRARY_PATH

if [[ ! -d "${LAYOUT_OPTIMIZER_DIR}" ]]; then
  echo "[ensure_latest_map] 错误: layout-optimizer 目录不存在: ${LAYOUT_OPTIMIZER_DIR}" >&2
  exit 1
fi

mkdir -p "${MAP_OUTPUT_DIR}/nav_graphs" "${MAP_OUTPUT_DIR}/models"

echo "[ensure_latest_map] 编译 layout-optimizer → ${MAP_OUTPUT_DIR}"
COMPILE_ARGS=(--dir "${LAYOUT_OPTIMIZER_DIR}" --out "${MAP_OUTPUT_DIR}" --lab-uuid "${LAB_UUID}")
if [[ -n "${SCENE_JSON:-}" ]]; then
  COMPILE_ARGS+=(--scene "${SCENE_JSON}")
fi
# 黑点导航模型（#18 §10.5）：building.yaml 顶点=黑点 dock_* + 走廊折线，去掉所有 nav_*
if [[ "${BLACK_DOTS:-1}" == "1" ]]; then
  if [[ -f "${MAP_OUTPUT_DIR}/rmf_agv_routes.json" ]]; then
    COMPILE_ARGS+=(--black-dots)
  else
    echo "[ensure_latest_map] 警告: 缺 rmf_agv_routes.json，退回 nav_* 网格（先跑 agv_routes 生成）" >&2
  fi
fi
python3 "${REPO_ROOT}/Uni-Lab-OS/scripts/rmf_compile_layout_optimizer.py" "${COMPILE_ARGS[@]}"

echo "[ensure_latest_map] 注入仿真充电桩 (${ROBOT_NAME} @ ${CHARGER_WAYPOINT})"
BUILDING_YAML="${MAP_OUTPUT_DIR}/${LAB_UUID}.building.yaml"
python3 "${SCRIPT_DIR}/scripts/inject_fleet_chargers.py" \
  --building "${BUILDING_YAML}" \
  --robot "${ROBOT_NAME}:${CHARGER_WAYPOINT}"

# fleet_config 来源：默认 maps/fleet_config.yaml；FLEET_CONFIG 环境变量可覆盖（多机估算用 N 车临时配置）
cp -f "${FLEET_CONFIG:-${SCRIPT_DIR}/maps/fleet_config.yaml}" "${MAP_OUTPUT_DIR}/fleet_config.yaml"
echo "[ensure_latest_map] fleet_config ← ${FLEET_CONFIG:-${SCRIPT_DIR}/maps/fleet_config.yaml}"

set +u
source /opt/ros/humble/setup.bash
source /home/z43/ros2sp/install/setup.bash
set -u
export ROS_DOMAIN_ID=0

echo "[ensure_latest_map] 生成 nav_graph"
ros2 run rmf_building_map_tools building_map_generator nav \
  "${BUILDING_YAML}" \
  "${MAP_OUTPUT_DIR}/nav_graphs"

# 统一覆盖 nav_graph lane 速度上限（默认 1.5 m/s，可由 RMF_SPEED_LIMIT_MPS 覆盖）。
NAV_GRAPH_YAML="${MAP_OUTPUT_DIR}/nav_graphs/0.yaml"
if [[ -f "${NAV_GRAPH_YAML}" ]]; then
  python3 - <<PY
import yaml

path = "${NAV_GRAPH_YAML}"
speed = float("${RMF_SPEED_LIMIT_MPS:-1.5}")
with open(path, "r", encoding="utf-8") as f:
    doc = yaml.safe_load(f) or {}
for level in (doc.get("levels") or {}).values():
    lanes = level.get("lanes") or []
    for lane in lanes:
        if not isinstance(lane, list):
            continue
        if len(lane) < 3 or not isinstance(lane[2], dict):
            while len(lane) < 3:
                lane.append({})
            lane[2] = {}
        lane[2]["speed_limit"] = speed
with open(path, "w", encoding="utf-8") as f:
    yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
print(f"[ensure_latest_map] lane speed_limit 已统一为 {speed} m/s → {path}")
PY
fi

echo "[ensure_latest_map] 生成 Gazebo world"
ros2 run rmf_building_map_tools building_map_generator gazebo \
  "${BUILDING_YAML}" \
  "${MAP_OUTPUT_DIR}/unilab_layout.world" \
  "${MAP_OUTPUT_DIR}/models"

python3 "${SCRIPT_DIR}/scripts/patch_ign_world.py" \
  --world "${MAP_OUTPUT_DIR}/unilab_layout.world"

MANIFEST="${MAP_OUTPUT_DIR}/manifest.json"
python3 - <<PY
import json, os, time
from pathlib import Path
out = Path("${MAP_OUTPUT_DIR}")
manifest = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "layout_optimizer_dir": "${LAYOUT_OPTIMIZER_DIR}",
    "lab_uuid": "${LAB_UUID}",
    "fleet_name": "${FLEET_NAME}",
    "building_yaml": str(out / "${LAB_UUID}.building.yaml"),
    "nav_graph": str(out / "nav_graphs" / "0.yaml"),
    "fleet_config": str(out / "fleet_config.yaml"),
    "world": str(out / "unilab_layout.world"),
    "models_dir": str(out / "models"),
    "floorplan": str(out / "L1_floorplan.png"),
    "semantic_map": str(out / "semantic_map.json"),
    "transfer_plan": str(out / "rmf_transfer_plan.json"),
    "path_catalog": str(out / "rmf_nav_path_catalog.json"),
}
(out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(manifest, ensure_ascii=False, indent=2))
PY

echo "[ensure_latest_map] 完成 → ${MANIFEST}"

