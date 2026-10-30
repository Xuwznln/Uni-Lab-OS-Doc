#!/usr/bin/env bash
# 对齐 GitHub CI Check 的 python-suite（无 ROS + 覆盖率门槛）。由 unit_tests_python_suite 调用。
set -euo pipefail
source "$(dirname "$0")/unit-test-cache.sh"

cache_root="$(unit_test_cache_root)"
mkdir -p "$cache_root"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$cache_root/.lock"
  flock 9
fi
echo "unit-test cache: $cache_root"
workers="$(nproc 2>/dev/null || echo 1)"
echo "pytest workers: $workers"

unit_test_ensure_node
node --version
npm --version
unit_test_ensure_frontend_modules
(cd frontend && npm run build)
test -s unilabos/app/web/static/console/index.html

req_key="$(unit_test_sha256 unilabos/utils/requirements.txt)"
setup_key="$(unit_test_sha256 setup.py)"
env_key="${req_key}-${setup_key}-py311-xdist1"
env_line="$(unit_test_prepare_env python-suite "$env_key")"
env_state="${env_line%% *}"
prefix="${env_line#* }"
py="$prefix/bin/python"
echo "python env ${env_state}: $prefix"
if [[ "$env_state" == miss ]]; then
  "$py" -m pip install --no-cache-dir -r unilabos/utils/requirements.txt
  "$py" -m pip install --no-cache-dir pytest pytest-cov pytest-xdist "pylabrobot==0.2.1"
  "$py" -m pip install --no-cache-dir .
  unit_test_commit_env "$prefix" "$env_key"
else
  "$py" -m pip install --disable-pip-version-check --no-deps .
fi

mapfile -t paths < <(grep -vE '^(#|[[:space:]]*$)' tests/ci_python_suite.txt)
mapfile -t ignores < <(grep -vE '^(#|[[:space:]]*$)' tests/ci_ros_required.txt)
ignore_args=()
for item in "${ignores[@]}"; do
  ignore_args+=(--ignore="$item")
done
"$py" -m pytest -q -n "$workers" --dist loadfile "${paths[@]}" "${ignore_args[@]}" \
  --cov=unilabos \
  --cov-branch \
  --cov-report=term \
  --cov-report=json:repository-coverage.json
"$py" scripts/check_python_suite_coverage.py \
  repository-coverage.json \
  --statements 67 \
  --branches 54
