#!/usr/bin/env bash
# 完整重启 layout-optimizer RMF 全栈（供自测用）。
# 顺序：停服 → 刷新地图 → api-server → RMF+Gazebo → Dashboard → OS edge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}"
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

can_open_popup() {
  [[ "${POPUP_TERMINALS:-1}" == "1" ]] || return 1
  command -v gnome-terminal >/dev/null 2>&1 || return 1
  [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]] || return 1
}

start_with_popup() {
  local title="$1"
  local run_cmd="$2"
  local log_file="$3"
  if ! can_open_popup; then
    return 1
  fi
  gnome-terminal --title="${title}" -- bash -lc "
    export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    cd \"${SCRIPT_DIR}\"
    set -o pipefail
    ${run_cmd} 2>&1 | tee -a \"${log_file}\"
    cmd_code=\${PIPESTATUS[0]}
    exit \${cmd_code}
  " >/dev/null 2>&1 &
  disown || true
  return 0
}

bash "${SCRIPT_DIR}/stop_all_rmf.sh"

echo "[restart_all_rmf] 启动 api-server..."
setsid bash "${SCRIPT_DIR}/run_rmfweb.sh" > "${LOG_DIR}/rmfweb.log" 2>&1 < /dev/null &
disown || true

for i in $(seq 1 30); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/docs; then
    echo "[restart_all_rmf] api-server OK (:8000)"
    break
  fi
  sleep 1
  if [[ "${i}" -eq 30 ]]; then
    echo "[restart_all_rmf] api-server 启动超时，见 ${LOG_DIR}/rmfweb.log" >&2
    tail -20 "${LOG_DIR}/rmfweb.log" >&2 || true
    exit 1
  fi
done

echo "[restart_all_rmf] 启动 RMF core + Gazebo（含 ensure_latest_map）..."
if start_with_popup "RMF Core + Gazebo" "bash \"${SCRIPT_DIR}/run_rmf_layout.sh\"" "${LOG_DIR}/rmf_layout.log"; then
  echo "[restart_all_rmf] RMF 以弹窗终端启动（日志同步到 ${LOG_DIR}/rmf_layout.log）"
else
  setsid bash "${SCRIPT_DIR}/run_rmf_layout.sh" > "${LOG_DIR}/rmf_layout.log" 2>&1 < /dev/null &
  disown || true
  echo "[restart_all_rmf] RMF 弹窗不可用，已回退后台模式"
fi

for i in $(seq 1 90); do
  if pgrep -f 'building_map_server.*demo_lab' >/dev/null && pgrep -f 'fleet_adapter.*fleet_config' >/dev/null; then
    echo "[restart_all_rmf] RMF core OK"
    break
  fi
  sleep 2
  if [[ "${i}" -eq 90 ]]; then
    echo "[restart_all_rmf] RMF 启动较慢，继续启动 Dashboard（见 ${LOG_DIR}/rmf_layout.log）" >&2
  fi
done

echo "[restart_all_rmf] 启动 Dashboard..."
bash "${SCRIPT_DIR}/run_dashboard.sh"

echo "[restart_all_rmf] 启动 OS edge..."
if start_with_popup "OS Edge" "bash \"${SCRIPT_DIR}/run_edge.sh\"" "${LOG_DIR}/edge.log"; then
  echo "[restart_all_rmf] OS edge 以弹窗终端启动（日志同步到 ${LOG_DIR}/edge.log）"
else
  setsid bash "${SCRIPT_DIR}/run_edge.sh" > "${LOG_DIR}/edge.log" 2>&1 < /dev/null &
  disown || true
  echo "[restart_all_rmf] OS edge 弹窗不可用，已回退后台模式"
fi

sleep 3
echo ""
echo "========== 服务状态 =========="
ss -tlnp 2>/dev/null | grep -E ':8000|:8002|:5173|:8006' || true
pgrep -af 'building_map_server.*demo_lab' | head -1 || echo "building_map_server: 未就绪"
pgrep -af 'fleet_adapter.*fleet_config' | head -1 || echo "fleet_adapter: 未就绪"
echo ""
echo "Dashboard:  http://localhost:5173"
echo "api-server: http://localhost:8000/docs"
echo "OS edge:    http://localhost:8002"
echo "轨迹 WS:    ws://localhost:8006"
echo "路径工作台: http://localhost:5180/path_studio/  (bash run_path_studio.sh)"
echo "日志: ${LOG_DIR}/{rmfweb,rmf_layout,edge,dashboard_vite}.log"

