import runpy
import shutil
from pathlib import Path

from setuptools import Distribution, find_packages, setup
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel


class NativeDistribution(Distribution):
    """串行库绑定操作系统和架构，不能发布为通用 wheel。"""

    def has_ext_modules(self):
        return True


class NativeWheel(bdist_wheel):
    """原生库没有 CPython ABI 依赖，可由同架构 Python 3.11+ 加载。"""

    def get_tag(self):
        _, _, platform = super().get_tag()
        return "py3", "none", platform


class BuildWithSerialLibrary(build_py):
    """从公开源码包也能构建默认串行库，不读取任何商业算法。"""

    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        package_root = Path(self.build_lib) / "unilabos/scheduling"
        # 复用旧 build 目录也不能夹带历史商业实现或旧架构的动态库。
        for name in ("cpsat", "queue_native"):
            shutil.rmtree(package_root / name, ignore_errors=True)
        shutil.rmtree(package_root.parent / "devices", ignore_errors=True)
        builder = runpy.run_path(str(root / "native/scheduler-queue/build.py"))
        builder["build"](package_root / "queue_native")

package_name = 'unilabos'

setup(
    name=package_name,
    version='1.0.0',
    python_requires='>=3.11',
    distclass=NativeDistribution,
    cmdclass={'build_py': BuildWithSerialLibrary, 'bdist_wheel': NativeWheel},
    # 公开宿主只交付队列、协议与插件装配；CP-SAT 源码由独立原生构建使用。
    packages=find_packages(exclude=[
        'unilabos.scheduling.cpsat', 'unilabos.scheduling.cpsat.*',
        'unilabos.devices', 'unilabos.devices.*',
    ]),
    include_package_data=True,
    package_data={
        'unilabos.scheduling': ['contract/*.json', 'contract/*.h', 'contract/*.md', 'resource-bindings-v1.md'],
    },
    install_requires=[
        'setuptools',
        'jsonschema>=4.18',
        'rfc8785>=0.1.4,<0.2',
        'fastapi>=0.115,<1',
        'networkx>=3,<4',
        'pint>=0.24,<1',
        'requests>=2,<3',
        'pydantic>=2,<3',
        'msgcenterpy>=0.1.8,<0.2',
        'pylabrobot==0.2.1',
        'python-multipart>=0.0.9,<1',
        'openpyxl>=3.1,<4',
    ],
    extras_require={
        'mcp': ['mcp>=1.10,<2'],
        'scheduling-cpsat': [],
    },
    zip_safe=False,
    author="The unilabos developers",
    maintainer='Junhan Chang, Xuwznln',
    maintainer_email='Junhan Chang <changjh@pku.edu.cn>, Xuwznln <18435084+Xuwznln@users.noreply.github.com>',
    description='',
    license='GPL v3',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            "unilab = unilabos.app.main:main",
            "unilab-supervisor = unilabos.managed_runtime.supervisor:main",
            "unilab-mcp = unilabos.agent_tools.workflow:main",
        ],
    },
)
