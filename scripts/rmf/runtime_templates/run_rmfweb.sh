#!/usr/bin/env bash
# 启动 rmf-web api-server。
#
# 关键 1：必须从“干净环境”启动。若继承了父 shell 残留的
#   LD_LIBRARY_PATH / PYTHONPATH / AMENT_PREFIX_PATH（例如反复 conda activate / source 的污染），
#   overlay 的 rmf 消息 C 库会被旧版抢先解析，导致
#   UnsupportedTypeSupport: rmf_fleet_msgs__msg__emergency_signal__convert_from_py undefined symbol。
#   因此这里先用 env -i 重新 exec 自身，得到纯净环境。
# 关键 2：conda unilab(Python 3.11 / RoboStack ROS) + rmf_msgs_ws overlay（提供较新 rmf 消息）。

if [ -z "${_RMFWEB_CLEAN:-}" ]; then
  exec env -i \
    HOME="$HOME" USER="$USER" \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    _RMFWEB_CLEAN=1 \
    bash "$0" "$@"
fi

source /home/z43/miniforge3/etc/profile.d/conda.sh
conda activate unilab
source /home/z43/git_pj/LeapLab/.rmf_run_logs/rmf_msgs_ws/install/setup.bash

export PYTHONPATH="/home/z43/git_pj/LeapLab/rmf_sim/rmf-web/packages/api-server:${PYTHONPATH}"
export ROS_DOMAIN_ID=0
export RMF_API_SERVER_LOG_LEVEL=INFO

cd /home/z43/git_pj/LeapLab/.rmf_run_logs/rmfweb_run

echo "[run_rmfweb] python=$(which python) ver=$(python --version 2>&1)"
echo "[run_rmfweb] rclpy=$(python -c 'import rclpy;print(rclpy.__file__)' 2>&1)"

exec python -m api_server

