#!/usr/bin/env bash
# 启动 RMF Dashboard 前端(Vite dev, office demo)。
# 首次使用见 RUNBOOK.md「§4 前端首次准备」。
set -e

PKG=/home/z43/git_pj/LeapLab/rmf_sim/rmf-web/packages/rmf-dashboard-framework
NODE=/home/z43/git_pj/LeapLab/.rmf_run_logs/tools/node20/bin
LOG=/home/z43/git_pj/LeapLab/.rmf_run_logs/dashboard_vite.log

export PATH="${NODE}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [ ! -x "${NODE}/node" ]; then
  echo "[run_dashboard] 未找到 node,请先按 RUNBOOK.md §4 安装 Node 20" >&2
  exit 1
fi

if [ ! -d "${PKG}/node_modules/.vite" ] && [ ! -f "${PKG}/../../node_modules/.pnpm/lock.yaml" ]; then
  echo "[run_dashboard] 未找到前端依赖,请先按 RUNBOOK.md §4 执行 pnpm install" >&2
  exit 1
fi

cd "${PKG}"
# 用 setsid 脱离终端,避免父 shell 退出时 vite 被杀
setsid bash -c "
  export PATH='${NODE}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  cd '${PKG}'
  exec node_modules/.bin/vite --config vite.demo.config.ts --port 5173 --strictPort
" > "${LOG}" 2>&1 < /dev/null &
disown
sleep 2
if ss -tlnp 2>/dev/null | grep -q ':5173'; then
  echo "[run_dashboard] OK  http://localhost:5173/  (日志: ${LOG})"
else
  echo "[run_dashboard] 启动中或失败,查看日志: ${LOG}" >&2
  tail -20 "${LOG}" >&2
  exit 1
fi

