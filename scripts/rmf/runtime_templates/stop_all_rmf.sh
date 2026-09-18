#!/usr/bin/env bash
# 停止 layout-optimizer RMF 全栈（RMF core / api-server / Dashboard / OS edge）。
set -uo pipefail

echo "[stop_all_rmf] 停止服务..."

patterns=(
  'unilabos.app.main.*lab_rmf_sim'
  'rmf_os_read_tasks'
  'python -m api_server'
  'vite.*5173'
  'building_map_server.*demo_lab'
  'fleet_adapter.*fleet_config'
  'fleet_manager.*fleet_config'
  'ros2 launch.*unilab_layout'
  'ign gazebo.*unilab_layout'
  'schedule_visualizer_node'
  'rmf_visualization_'
  'door_supervisor'
  'lift_supervisor'
  'mutex_group_supervisor\.py'
  'strict_fleet_adapter\.py'
)

for pat in "${patterns[@]}"; do
  pkill -f "${pat}" 2>/dev/null || true
done

sleep 2
for pat in "${patterns[@]}"; do
  pkill -9 -f "${pat}" 2>/dev/null || true
done

# 兜底：按「完整二进制路径」精确清掉 RMF core 残留进程（pkill 的 node-name 模式抓不全多代孤儿，
# 它们留在 ROS_DOMAIN_ID=0 上互相冲突，会让 fleet_adapter 收到陈旧 DispatchRequest 而段错误）。
# 用 PID + 路径锚定，绝不误伤本 shell（其 cmdline 不含 /opt/ros 这些路径）。
MYPID=$$
RMF_PIDS=$(ps -eo pid,args 2>/dev/null | grep -E '/(opt/ros/humble|home/z43/ros2sp/install)/[^ ]*(rmf_traffic_schedule|rmf_task_dispatcher|rmf_traffic_blockade|building_map_server|door_supervisor|lift_supervisor|fleet_adapter|fleet_manager|rmf_visualization|schedule_visualizer|navgraph_visualizer)|ros2 launch[^ ]* [^ ]*unilab_layout|run_rmf_layout\.sh' | grep -v grep | awk -v me="$MYPID" '$1!=me{print $1}')
for p in ${RMF_PIDS}; do kill -9 "$p" 2>/dev/null || true; done

# edge mock + fleet_manager_http（按监听端口取 PID 清理；STOP_EDGE=0 可保留 edge）
if [[ "${STOP_EDGE:-1}" == "1" ]]; then
  for port in 8090 22011 22012; do
    pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
    [ -n "${pid:-}" ] && kill -9 "$pid" 2>/dev/null || true
  done
fi

sleep 1
echo "[stop_all_rmf] 完成"

