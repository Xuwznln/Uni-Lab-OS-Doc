"""旧内核测试兼容入口；统一生产调度不加载私有参考算法。"""

import sys

from unilabos.scheduling.cpsat.reference import time_scheduling as _implementation

sys.modules[__name__] = _implementation
