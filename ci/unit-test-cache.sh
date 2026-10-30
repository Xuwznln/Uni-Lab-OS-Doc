#!/usr/bin/env bash
# MR 流水线读取的是 main 上的 .gitlab-ci.yml，分支里的 cache: 不会生效。
# 这台 Runner 会保留 /builds，并且只 git clean 项目目录，所以工具链放在项目外。

unit_test_cache_root() {
  if [[ -n "${UNIT_TEST_CACHE_ROOT:-}" ]]; then
    printf '%s\n' "$UNIT_TEST_CACHE_ROOT"
    return
  fi
  local parent candidate
  for candidate in /builds/lab-os/.ci-cache /cache/uni-lab-os; do
    parent="$(dirname "$candidate")"
    if [[ -d "$parent" && -w "$parent" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  printf '%s\n' "${CI_PROJECT_DIR:-$PWD}/.ci-local-cache"
}

unit_test_sha256() {
  sha256sum "$1" | awk '{print $1}'
}

unit_test_curl() {
  curl --fail --location --retry 5 --retry-delay 2 \
    --connect-timeout 20 --speed-time 30 --speed-limit 10000 \
    "$@"
}

unit_test_ensure_os_packages() {
  if command -v curl >/dev/null 2>&1 \
    && command -v gcc >/dev/null 2>&1 \
    && command -v sha256sum >/dev/null 2>&1; then
    return
  fi
  apt-get update -qq >&2
  apt-get install -y --no-install-recommends \
    ca-certificates curl bzip2 tar xz-utils build-essential git >&2
}

unit_test_micromamba() {
  local root="$1"
  local bin="$root/bin/micromamba"
  if [[ ! -x "$bin" ]]; then
    mkdir -p "$root/bin"
    unit_test_curl --output "$root/micromamba.tar.bz2" \
      'https://micro.mamba.pm/api/micromamba/linux-64/2.8.1'
    tar -xjf "$root/micromamba.tar.bz2" -C "$root" bin/micromamba
  fi
  printf '%s\n' "$bin"
}

# 标准输出只有 "hit <prefix>" 或 "miss <prefix>"。依赖安装由调用方完成后再提交 key。
unit_test_prepare_env() {
  local name="$1"
  local key="$2"
  local root prefix recorded
  root="$(unit_test_cache_root)"
  prefix="$root/envs/$name"
  recorded=""
  if [[ -f "$prefix/.cache-key" ]]; then
    recorded="$(<"$prefix/.cache-key")"
  fi
  if [[ -x "$prefix/bin/python" && "$recorded" == "$key" ]]; then
    printf 'hit %s\n' "$prefix"
    return
  fi
  unit_test_ensure_os_packages
  local mamba
  mamba="$(unit_test_micromamba "$root")"
  rm -rf "$prefix"
  "$mamba" create -y -p "$prefix" -c conda-forge python=3.11 pip >&2
  printf 'miss %s\n' "$prefix"
}

unit_test_commit_env() {
  local prefix="$1"
  local key="$2"
  printf '%s\n' "$key" >"$prefix/.cache-key"
}

unit_test_ensure_node() {
  local version="22.22.0"
  local root dest
  root="$(unit_test_cache_root)"
  dest="$root/node-v${version}-linux-x64"
  if [[ ! -x "$dest/bin/node" ]]; then
    unit_test_ensure_os_packages
    mkdir -p "$root"
    unit_test_curl --output "$root/node-v${version}-linux-x64.tar.xz" \
      "https://nodejs.org/dist/v${version}/node-v${version}-linux-x64.tar.xz"
    tar -xJf "$root/node-v${version}-linux-x64.tar.xz" -C "$root"
  fi
  export PATH="$dest/bin:${PATH}"
}

unit_test_ensure_frontend_modules() {
  local root lock_hash dest
  root="$(unit_test_cache_root)"
  lock_hash="$(unit_test_sha256 frontend/package-lock.json)"
  dest="$root/frontend-modules"
  if [[ -d "$dest/node_modules" && -f "$dest/.lock" && "$(<"$dest/.lock")" == "$lock_hash" ]]; then
    rm -rf frontend/node_modules
    cp -a "$dest/node_modules" frontend/node_modules
    return
  fi
  mkdir -p "$root/npm"
  (cd frontend && npm ci --cache "$root/npm" --prefer-offline)
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -a frontend/node_modules "$dest/node_modules"
  printf '%s\n' "$lock_hash" >"$dest/.lock"
}
