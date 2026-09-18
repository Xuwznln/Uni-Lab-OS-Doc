#!/usr/bin/env bash
# 启动 Uni-Lab-OS edge 常驻服务(含 RMF 调度协调设备)。
# 关键点:
#  - 用工作树 unilabos(安装的副本是旧版,缺 RMF 集成),PYTHONPATH 前置工作树
#  - conda unilab 提供 3.11 版 rclpy(RoboStack),与 RMF core 经 DDS 通信
#  - 前置 rmf_msgs_ws overlay 的 3.11 消息包,ROS_DOMAIN_ID=0 对齐 RMF core
#  - 桥按 ak/sk 自动选：有 key -> websocket+fastapi(联网/上报 cloud)，无 -> fastapi(离线)
#
# 关键(同 run_rmfweb.sh)：必须从【干净环境】启动。否则继承父 shell 残留的
#   AMENT_PREFIX_PATH / LD_LIBRARY_PATH / PYTHONPATH（例如曾 source 过 ros2sp/humble 的
#   ROS 2 Python 3.10），会让 conda(3.11) 进程误加载 ros2sp 的 3.10 unilabos_msgs 类型支持，
#   触发 UnsupportedTypeSupport / undefined symbol，HostNode 构造即崩、设备不初始化、车队主不上线。
#   故先用 env -i 重新 exec 自身，得到纯净环境再 conda activate。
if [ -z "${_EDGE_CLEAN:-}" ]; then
  exec env -i \
    HOME="$HOME" USER="$USER" \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DISPLAY="${DISPLAY:-}" \
    ROS_DOMAIN_ID=0 \
    UNILAB_AK="${UNILAB_AK:-}" UNILAB_SK="${UNILAB_SK:-}" UNILAB_ADDR="${UNILAB_ADDR:-}" \
    UNILAB_VISUAL="${UNILAB_VISUAL:-}" \
    UNILAB_FORCE_RUNTIME_MODE="${UNILAB_FORCE_RUNTIME_MODE:-}" \
    UNILAB_FORCE_SUPPORTS_SIM_CLOCK="${UNILAB_FORCE_SUPPORTS_SIM_CLOCK:-}" \
    _EDGE_CLEAN=1 \
    bash "$0" "$@"
fi
set -e

# 云端 AK/SK（实验室网页签发）。可用环境变量 UNILAB_AK / UNILAB_SK 覆盖；默认填入当前 lab 的 key。
# 提供了 ak+sk → 联网模式（websocket + fastapi 桥，连 cloud 上报）；否则离线（仅 fastapi）。
UNILAB_AK="${UNILAB_AK:-9df40621-446b-4e12-bce2-e461119219f9}"
UNILAB_SK="${UNILAB_SK:-f92d0708-0705-46b0-91cb-3be8b2f346a9}"
# 云端环境：test/uat/local 或自定义 URL（--addr）。test → https://leap-lab.test.bohrium.com/api/v1
UNILAB_ADDR="${UNILAB_ADDR:-test}"
# 可视化模式：rviz/web/disable（默认 rviz，便于本地调试）
UNILAB_VISUAL="${UNILAB_VISUAL:-rviz}"
# 联调兜底：强制 rmf.coordinator 上报 sim 能力，避免上下文探测异常导致 cloud 误判为 real
UNILAB_FORCE_RUNTIME_MODE="${UNILAB_FORCE_RUNTIME_MODE:-sim}"
UNILAB_FORCE_SUPPORTS_SIM_CLOCK="${UNILAB_FORCE_SUPPORTS_SIM_CLOCK:-1}"

case "${UNILAB_VISUAL}" in
  rviz|web|disable) ;;
  *)
    echo "[run_edge] 非法 UNILAB_VISUAL=${UNILAB_VISUAL}，回退 rviz"
    UNILAB_VISUAL="rviz"
    ;;
esac

source /home/z43/miniforge3/etc/profile.d/conda.sh
conda activate unilab

WORKTREE=/home/z43/git_pj/LeapLab/Uni-Lab-OS
DATA=/home/z43/git_pj/LeapLab/.rmf_run_logs/edge_data

# 前置 RMF 3.11 消息 overlay(coordinator 若做 ROS 通信需要)
OVERLAY_INSTALL=/home/z43/git_pj/LeapLab/.rmf_run_logs/rmf_msgs_ws/install
for d in "${OVERLAY_INSTALL}"/*/lib/python3.11/site-packages; do
  [ -d "$d" ] && PYTHONPATH="$d:${PYTHONPATH:-}"
done

# 工作树放最前,覆盖安装的旧副本
export PYTHONPATH="${WORKTREE}:${PYTHONPATH:-}"
export ROS_DOMAIN_ID=0

cd "${WORKTREE}"
echo "[run_edge] python=$(which python) ver=$(python --version 2>&1)"
echo "[run_edge] unilabos=$(python -c 'import unilabos;print(unilabos.__file__)' 2>&1)"

# 有 ak+sk → 联网（websocket+fastapi）；否则离线（fastapi）
AKSK_ARGS=()
BRIDGES=(fastapi)
if [ -n "${UNILAB_AK}" ] && [ -n "${UNILAB_SK}" ]; then
  # --upload_registry：把 edge 设备注册表上传云端（仅 ak/sk 时生效）
  AKSK_ARGS=(--ak "${UNILAB_AK}" --sk "${UNILAB_SK}" --addr "${UNILAB_ADDR}" --upload_registry)
  BRIDGES=(websocket fastapi)
  echo "[run_edge] 联网模式：--ak ${UNILAB_AK:0:8}… --sk **** --addr ${UNILAB_ADDR} --upload_registry（连 cloud；桥=${BRIDGES[*]}）"
else
  echo "[run_edge] 离线模式：未提供 ak/sk（桥=${BRIDGES[*]}）"
fi

# 输出同时进本窗口 + 日志文件（edge_os.log），便于在窗口外核实 OS/虚拟 AGV 日志。
OS_LOG=/home/z43/git_pj/LeapLab/.rmf_run_logs/edge_os.log
echo "[run_edge] OS 日志同时写: ${OS_LOG}"
python -u -m unilabos.app.main \
  --graph "${DATA}/lab_rmf_sim.json" \
  --scene /home/z43/scene_2026-06-18.json \
  --config "${DATA}/local_config.py" \
  --working_dir "${DATA}" \
  --backend ros \
  --app_bridges "${BRIDGES[@]}" \
  "${AKSK_ARGS[@]}" \
  --mode sim \
  --visual "${UNILAB_VISUAL}" \
  --disable_browser \
  --skip_env_check 2>&1 | tee "${OS_LOG}"

