"""
环境检查模块
用于检查并自动安装 UniLabOS 运行所需的 Python 包
"""

import argparse
import importlib
import locale
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from unilabos.utils.banner_print import print_status


# ---------------------------------------------------------------------------
# 底层安装工具
# ---------------------------------------------------------------------------

def _is_chinese_locale() -> bool:
    try:
        lang = locale.getdefaultlocale()[0]
        return bool(lang and ("zh" in lang.lower() or "chinese" in lang.lower()))
    except Exception:
        return False


_USE_UV: Optional[bool] = None


def _has_uv() -> bool:
    global _USE_UV
    if _USE_UV is None:
        _USE_UV = shutil.which("uv") is not None
    return _USE_UV


def _install_packages(
    packages: List[str],
    upgrade: bool = False,
    label: str = "",
) -> bool:
    """
    安装/升级一组包。优先 uv pip install，回退 sys pip。
    逐个安装，任意一个失败不影响后续包。

    Returns:
        True if all succeeded, False otherwise.
    """
    if not packages:
        return True

    is_chinese = _is_chinese_locale()
    use_uv = _has_uv()
    failed: List[str] = []

    for pkg in packages:
        action_word = "升级" if upgrade else "安装"
        if label:
            print_status(f"[{label}] 正在{action_word} {pkg}...", "info")
        else:
            print_status(f"正在{action_word} {pkg}...", "info")

        if use_uv:
            cmd = ["uv", "pip", "install"]
            if upgrade:
                cmd.append("--upgrade")
            cmd.append(pkg)
            if is_chinese:
                cmd.extend(["--index-url", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"])
        else:
            cmd = [sys.executable, "-m", "pip", "install"]
            if upgrade:
                cmd.append("--upgrade")
            cmd.append(pkg)
            if is_chinese:
                cmd.extend(["-i", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                installer = "uv" if use_uv else "pip"
                print_status(f"✓ {pkg} {action_word}成功 (via {installer})", "success")
            else:
                stderr_short = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
                print_status(f"× {pkg} {action_word}失败: {stderr_short}", "error")
                failed.append(pkg)
        except subprocess.TimeoutExpired:
            print_status(f"× {pkg} {action_word}超时 (300s)", "error")
            failed.append(pkg)
        except Exception as e:
            print_status(f"× {pkg} {action_word}异常: {e}", "error")
            failed.append(pkg)

    if failed:
        print_status(f"有 {len(failed)} 个包操作失败: {', '.join(failed)}", "error")
        return False
    return True


# ---------------------------------------------------------------------------
# requirements.txt 安装（可多次调用）
# ---------------------------------------------------------------------------

def install_requirements_txt(req_path: str | Path, label: str = "") -> bool:
    """
    读取一个 requirements.txt 文件，检查缺失的包并安装。

    Args:
        req_path: requirements.txt 文件路径
        label: 日志前缀标签（如 "device_package_sim"）

    Returns:
        True if all ok, False if any install failed.
    """
    req_path = Path(req_path)
    if not req_path.exists():
        return True

    tag = label or req_path.parent.name
    print_status(f"[{tag}] 检查依赖: {req_path}", "info")

    reqs: List[str] = []
    with open(req_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                reqs.append(line)

    if not reqs:
        return True

    missing: List[str] = []
    for req in reqs:
        pkg_import = req.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].split(">")[0].strip()
        pkg_import = pkg_import.replace("-", "_")
        try:
            importlib.import_module(pkg_import)
        except ImportError:
            missing.append(req)

    if not missing:
        print_status(f"[{tag}] ✓ 依赖检查通过 ({len(reqs)} 个包)", "success")
        return True

    print_status(f"[{tag}] 缺失 {len(missing)} 个依赖: {', '.join(missing)}", "warning")
    return _install_packages(missing, label=tag)


def check_device_package_requirements(devices_dirs: list[str]) -> bool:
    """
    检查 --devices 指定的所有外部设备包目录中的 requirements.txt。
    对每个目录查找 requirements.txt（先在目录内找，再在父目录找）。
    """
    if not devices_dirs:
        return True

    all_ok = True
    for d in devices_dirs:
        d_path = Path(d).resolve()
        req_file = d_path / "requirements.txt"
        if not req_file.exists():
            req_file = d_path.parent / "requirements.txt"
        if not req_file.exists():
            continue
        if not install_requirements_txt(req_file, label=d_path.name):
            all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# UniLabOS 核心环境检查
# ---------------------------------------------------------------------------

class EnvironmentChecker:
    """环境检查器"""

    def __init__(self):
        self.required_packages = {
            "websockets": "websockets",
            "msgcenterpy": "msgcenterpy",
            "orjson": "orjson",
            "opentrons_shared_data": "opentrons_shared_data",
            "typing_extensions": "typing_extensions",
            "crcmod": "crcmod-plus",
        }

        # 中文 locale 下走 Gitee 镜像，规避 GitHub 拉取失败
        pylabrobot_url = (
            "git+https://gitee.com/xuwznln/pylabrobot.git"
            if _is_chinese_locale()
            else "git+https://github.com/Xuwznln/pylabrobot.git"
        )
        self.special_packages = {"pylabrobot": pylabrobot_url}

        self.version_requirements = {
            "msgcenterpy": "0.1.8",
        }

        self.missing_packages: List[tuple] = []
        self.failed_installs: List[tuple] = []
        self.packages_need_upgrade: List[tuple] = []

    def check_package_installed(self, package_name: str) -> bool:
        try:
            importlib.import_module(package_name)
            return True
        except ImportError:
            return False

    def get_package_version(self, package_name: str) -> str | None:
        try:
            module = importlib.import_module(package_name)
            return getattr(module, "__version__", None)
        except (ImportError, AttributeError):
            return None

    def compare_version(self, current: str, required: str) -> bool:
        try:
            current_parts = [int(x) for x in current.split(".")]
            required_parts = [int(x) for x in required.split(".")]
            max_len = max(len(current_parts), len(required_parts))
            current_parts.extend([0] * (max_len - len(current_parts)))
            required_parts.extend([0] * (max_len - len(required_parts)))
            return current_parts >= required_parts
        except Exception:
            return True

    def check_all_packages(self) -> bool:
        print_status("开始检查环境依赖...", "info")

        for import_name, pip_name in self.required_packages.items():
            if not self.check_package_installed(import_name):
                self.missing_packages.append((import_name, pip_name))
            elif import_name in self.version_requirements:
                current_version = self.get_package_version(import_name)
                required_version = self.version_requirements[import_name]
                if current_version and not self.compare_version(current_version, required_version):
                    print_status(
                        f"{import_name} 版本过低 (当前: {current_version}, 需要: >={required_version})",
                        "warning",
                    )
                    self.packages_need_upgrade.append((import_name, pip_name))

        for package_name, install_url in self.special_packages.items():
            if not self.check_package_installed(package_name):
                self.missing_packages.append((package_name, install_url))

        all_ok = not self.missing_packages and not self.packages_need_upgrade

        if all_ok:
            print_status("✓ 所有依赖包检查完成，环境正常", "success")
            return True

        if self.missing_packages:
            print_status(f"发现 {len(self.missing_packages)} 个缺失的包", "warning")
        if self.packages_need_upgrade:
            print_status(f"发现 {len(self.packages_need_upgrade)} 个需要升级的包", "warning")

        return False

    def install_missing_packages(self, auto_install: bool = True) -> bool:
        if not self.missing_packages and not self.packages_need_upgrade:
            return True

        if not auto_install:
            if self.missing_packages:
                print_status("缺失以下包:", "warning")
                for import_name, pip_name in self.missing_packages:
                    print_status(f"  - {import_name} ({pip_name})", "warning")
            if self.packages_need_upgrade:
                print_status("需要升级以下包:", "warning")
                for import_name, pip_name in self.packages_need_upgrade:
                    print_status(f"  - {import_name} ({pip_name})", "warning")
            return False

        if self.missing_packages:
            pkgs = [pip_name for _, pip_name in self.missing_packages]
            if not _install_packages(pkgs, label="unilabos"):
                self.failed_installs.extend(self.missing_packages)

        if self.packages_need_upgrade:
            pkgs = [pip_name for _, pip_name in self.packages_need_upgrade]
            if not _install_packages(pkgs, upgrade=True, label="unilabos"):
                self.failed_installs.extend(self.packages_need_upgrade)

        return not self.failed_installs

    def verify_installation(self) -> bool:
        if not self.missing_packages and not self.packages_need_upgrade:
            return True

        print_status("验证安装结果...", "info")
        failed_verification = []

        for import_name, pip_name in self.missing_packages:
            if not self.check_package_installed(import_name):
                failed_verification.append((import_name, pip_name))

        for import_name, pip_name in self.packages_need_upgrade:
            if not self.check_package_installed(import_name):
                failed_verification.append((import_name, pip_name))
            elif import_name in self.version_requirements:
                current_version = self.get_package_version(import_name)
                required_version = self.version_requirements[import_name]
                if current_version and not self.compare_version(current_version, required_version):
                    failed_verification.append((import_name, pip_name))
                    print_status(
                        f"  {import_name} 版本仍然过低 (当前: {current_version}, 需要: >={required_version})",
                        "error",
                    )

        if failed_verification:
            print_status(f"有 {len(failed_verification)} 个包验证失败:", "error")
            for import_name, pip_name in failed_verification:
                print_status(f"  - {import_name}", "error")
            return False

        print_status("✓ 所有包验证通过", "success")
        return True


def check_environment(auto_install: bool = True, show_details: bool = True) -> bool:
    """
    检查环境并自动安装缺失的包

    Args:
        auto_install: 是否自动安装缺失的包
        show_details: 是否显示详细信息

    Returns:
        bool: 环境检查是否通过
    """
    checker = EnvironmentChecker()

    if checker.check_all_packages():
        return True

    if not checker.install_missing_packages(auto_install):
        if show_details:
            print_status("请手动安装缺失的包后重新启动程序", "error")
        return False

    if not checker.verify_installation():
        if show_details:
            print_status("安装验证失败，请检查网络连接或手动安装", "error")
        return False

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UniLabOS 环境依赖检查工具")
    parser.add_argument("--no-auto-install", action="store_true", help="仅检查环境，不自动安装缺失的包")
    parser.add_argument("--silent", action="store_true", help="静默模式，不显示详细信息")

    args = parser.parse_args()

    auto_install = not args.no_auto_install
    show_details = not args.silent

    success = check_environment(auto_install=auto_install, show_details=show_details)

    if not success:
        if show_details:
            print_status("环境检查失败", "error")
        sys.exit(1)
    else:
        if show_details:
            print_status("环境检查完成", "success")
