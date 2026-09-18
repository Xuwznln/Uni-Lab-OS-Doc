#!/usr/bin/env bash
# 启动 RMF core：自动编译最新 layout-optimizer 地图并加载（替代 office demo）。
#
# 用法:
#   bash run_rmf_layout.sh              # Gazebo 仿真（默认）
#   RMF_LAYOUT_MODE=headless bash run_rmf_layout.sh   # 仅调度 + 地图，无 Gazebo
#
# 前置：api-server 建议先启动（server_uri 推送机器人状态给 Dashboard）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/maps/map.env"

RMF_LAYOUT_MODE="${RMF_LAYOUT_MODE:-sim}"

echo "[run_rmf_layout] 刷新最新地图..."
bash "${SCRIPT_DIR}/ensure_latest_map.sh"

# ensure_latest_map 会重写 nav_graphs/0.yaml；必须在此后、RMF 加载前再打补丁。
PATCH_PY="${SCRIPT_DIR}/scripts/patch_nav_graph_holding.py"
if [[ -f "${PATCH_PY}" ]]; then
  echo "[run_rmf_layout] 应用 nav_graph holding 补丁..."
  python3 "${PATCH_PY}" "${MAP_OUTPUT_DIR}"
fi

export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_PROMPT_MODIFIER LD_LIBRARY_PATH
set +u
source /opt/ros/humble/setup.bash
source /home/z43/ros2sp/install/setup.bash
set -u
export ROS_DOMAIN_ID=0
export DISPLAY="${DISPLAY:-:0}"

MAPS=$(ros2 pkg prefix rmf_demos_maps)/share/rmf_demos_maps
ASSETS=$(ros2 pkg prefix rmf_demos_assets)/share/rmf_demos_assets
RSGP=$(ros2 pkg prefix rmf_robot_sim_gz_plugins)/lib/rmf_robot_sim_gz_plugins
RBGP=$(ros2 pkg prefix rmf_building_sim_gz_plugins)/lib/rmf_building_sim_gz_plugins
export IGN_GAZEBO_RESOURCE_PATH="${MAPS}/maps/office/models:${MAPS}/maps/office_ign/models:${ASSETS}/models:${HOME}/.gazebo/models"
export IGN_GAZEBO_SYSTEM_PLUGIN_PATH="${RSGP}:${RBGP}"
export GZ_SIM_RESOURCE_PATH="${MAP_OUTPUT_DIR}/models:${ASSETS}/models:${MAPS}/maps/office/models:${HOME}/.gazebo/models"

cd "${REPO_ROOT}"

if [[ "${RMF_LAYOUT_MODE}" == "headless" ]]; then
  LAUNCH_FILE="${SCRIPT_DIR}/launch/unilab_layout_headless.launch.xml"
  # USE_SIM_TIME=true：fleet_adapter 跟随外部 /clock（由 _rmf_sim_bridge 的 SimClockPublisher 提供），
  # 供 rmf_estimate_times.py --execute 的 sim 快进；默认 false（墙钟，planned 估时用）。
  echo "[run_rmf_layout] 启动 RMF headless（地图: ${MAP_OUTPUT_DIR}，use_sim_time=${USE_SIM_TIME:-false}）"
  exec ros2 launch "${LAUNCH_FILE}" \
    map_dir:="${MAP_OUTPUT_DIR}" \
    lab_uuid:="${LAB_UUID}" \
    use_sim_time:="${USE_SIM_TIME:-false}" \
    server_uri:="ws://localhost:8000/_internal" \
    script_dir:="${SCRIPT_DIR}"
fi

LAUNCH_FILE="${SCRIPT_DIR}/launch/unilab_layout.launch.xml"
echo "[run_rmf_layout] 启动 RMF + Gazebo（地图: ${MAP_OUTPUT_DIR}）"
exec ros2 launch "${LAUNCH_FILE}" \
  map_dir:="${MAP_OUTPUT_DIR}" \
  lab_uuid:="${LAB_UUID}" \
  headless:=true \
  server_uri:="ws://localhost:8000/_internal"

