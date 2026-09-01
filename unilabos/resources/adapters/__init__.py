"""外部运行时与微后端协议之间的适配器。

- ``plr_materials``：PLR 资源与物料权威之间的创建草稿/上传/下载；
- ``registry_materials``：Registry 定义登记与模板 UUID 映射；
- ``device_site``：设备注册表 ``available_sites`` 与实例 ``sites`` 校验。
"""

from unilabos.resources.adapters.device_site import *  # noqa: F403
from unilabos.resources.adapters.plr_materials import *  # noqa: F403
from unilabos.resources.adapters.registry_materials import *  # noqa: F403
