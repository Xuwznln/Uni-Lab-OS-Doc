#!/usr/bin/env bash
# 从源码编译整套 Open-RMF (humble) 到 $PREFIX，产出可分发、可在任意 osx-arm64
# 机器复用的 conda 包。源码由 recipe.yaml 的多个 git source 固定到已验证可用的
# humble commit 并打上 macOS 构建补丁（patches/ 下），本脚本只负责用 colcon 驱动
# 整个工作区的编译与安装。
#
# 关键点（均来自本机已验证可运行的 humble macOS arm64 构建）：
#   * 用 colcon --merge-install 直接装进 $PREFIX，布局与 conda 前缀一致；
#   * RMW 选 cyclonedds；编译期不依赖运行期 RMW，但消息包的 typesupport 需要它；
#   * 跳过 4 个在 macOS 上无用/编译失败的包（gazebo-classic 插件 + Qt traffic-editor GUI）；
#   * 全局 -include cassert：沙箱里干净的 conda clang/libc++ 不再隐式传递 <cassert>，
#     而 rmf_traffic 自身 ~25 个源文件与 vendored fcl 多处直接用 assert()，逐文件打补丁
#     太脆，强制前置包含一次性覆盖所有现存及未来文件，对纯 C 的 rosidl 代码无影响；
#   * 全局 -I$PREFIX/include/eigen3：conda 的 Eigen 头在 include/eigen3 子目录，
#     ament_target_dependencies(rmf_traffic 等) 不会把该 include 传播给下游消费者
#     （'Eigen/Geometry' file not found 在 floorplans/robot_sim/examples 反复出现），
#     全局加上该路径一次性覆盖所有用到 rmf_traffic 头（间接含 Eigen）的包；
#   * 编译后补齐 CycloneDDS 按叶名 dlopen 所需的 introspection typesupport dylib 软链。
set -euo pipefail

SRC_WS="$SRC_DIR/src"
PYVER="3.11"

# colcon/ament 需要能找到 host 环境里的 ROS 2 与 RMF 的依赖。
export CMAKE_PREFIX_PATH="$PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export AMENT_PREFIX_PATH="$PREFIX${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export PYTHONPATH="$PREFIX/lib/python${PYVER}/site-packages${PYTHONPATH:+:$PYTHONPATH}"

# 规范的 site-packages，让 ament_cmake_python 把 py 模块装到 conda 标准位置。
FIXED_SP_DIR="$($PREFIX/bin/python -c 'import site; print(site.getsitepackages()[0])')"

# macOS SDK：补丁里的 FindLibUUID.cmake 依赖 CMAKE_OSX_SYSROOT 指向真实 SDK。
OSX_SYSROOT="${CONDA_BUILD_SYSROOT:-}"

# 在 macOS 上无用或编译失败、且 demo 运行不需要的包：
#   *_gz_classic_plugins —— 用的是 Ignition Fortress，不用 gazebo-classic；
#   rmf_demos_gz_classic —— 依赖上面两个 gazebo-classic 插件包，同样不用；
#   rmf_traffic_editor(GUI)/test_maps —— Qt 重型编辑器，运行 demo 不需要。
SKIP_PKGS=(
  rmf_building_sim_gz_classic_plugins
  rmf_robot_sim_gz_classic_plugins
  rmf_demos_gz_classic
  rmf_traffic_editor
  rmf_traffic_editor_test_maps
)

echo "=== [1/3] colcon build 整个 RMF 工作区 -> \$PREFIX ==="
cd "$SRC_DIR"
colcon build \
  --base-paths "$SRC_WS" \
  --merge-install \
  --install-base "$PREFIX" \
  --packages-skip "${SKIP_PKGS[@]}" \
  --event-handlers console_direct+ \
  --cmake-args \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_PREFIX_PATH="$PREFIX" \
    -DAMENT_PREFIX_PATH="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DPYTHON_EXECUTABLE="$PREFIX/bin/python" \
    -DPython3_EXECUTABLE="$PREFIX/bin/python" \
    -DPython_EXECUTABLE="$PREFIX/bin/python" \
    -DPYTHON_INSTALL_DIR="$FIXED_SP_DIR" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DNO_DOWNLOAD_MODELS=ON \
    -DCMAKE_CXX_FLAGS="-include cassert -I$PREFIX/include/eigen3" \
    ${OSX_SYSROOT:+-DCMAKE_OSX_SYSROOT="$OSX_SYSROOT"} \
    -DCMAKE_FIND_FRAMEWORK=LAST \
  --no-warn-unused-cli

echo "=== [2/3] 补齐 CycloneDDS 按叶名 dlopen 所需的 typesupport dylib 软链 ==="
# rmf 的消息包在运行期通过 introspection typesupport 被 CycloneDDS 以叶文件名
# dlopen。merge-install 后这些 dylib 已在 $PREFIX/lib，但 ament 资源索引里登记的
# 是带包前缀的路径；这里在 lib 下补叶名软链，确保异机加载可用。
( cd "$PREFIX/lib" && \
  for f in lib*__rosidl_typesupport_introspection_c.dylib \
           lib*__rosidl_typesupport_introspection_cpp.dylib; do
    [ -e "$f" ] || continue
    leaf="${f#lib}"
    [ -e "$leaf" ] || ln -sf "$f" "$leaf" 2>/dev/null || true
  done ) || true

echo "=== [3/3] 清理 colcon 工作区脚本（conda activate 已提供 ament 环境） ==="
rm -f "$PREFIX"/setup.* "$PREFIX"/local_setup.* "$PREFIX"/_local_setup_util_*.py \
      "$PREFIX"/COLCON_IGNORE 2>/dev/null || true

echo "=== 完成。\$PREFIX/lib dylib 数：$(find "$PREFIX/lib" -maxdepth 1 -name '*.dylib' 2>/dev/null | wc -l | tr -d ' ') ==="
