"""兼容旧导入路径；算法实现由可替换引擎包持有。"""

import sys

from unilabos.scheduling.cpsat.reference import planning as _implementation

sys.modules[__name__] = _implementation
