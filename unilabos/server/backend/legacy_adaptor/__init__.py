"""旧后端上联适配层（``edge -> 微后端 -> 旧后端`` 的最后一跳）。

进程分层中，微后端对 Edge 扮演 Backend（见 ``server.backend.edge_control``）；
本包收敛微后端对**旧后端（云端 Backend）**的全部上联适配：

- ``session``   连接会话抽象与 ``comm_client`` 工厂（``get_backend_client``）；
- ``websocket`` runtime.v1 控制面 WebSocket 轻通知客户端；
- ``http``      HTTP 数据面客户端（WS 只通知变化，权威正文经此拉取）；
- ``url``       连接地址构建；
- ``sync``      各数据域向正式后端的显式同步（模板图 / 资源实例）。

调度权威、执行 bridge 与 Edge 控制面等本地微后端代码留在
``server.backend`` 直下，不属于本包。
"""

from unilabos.server.backend.legacy_adaptor.http import (
    BackendHTTPClient,
    BackendHTTPError,
)
from unilabos.server.backend.legacy_adaptor.session import (
    BackendSessionFactory,
    BaseBackendClient,
    get_backend_client,
)
from unilabos.server.backend.legacy_adaptor.websocket import BackendWebSocketClient

__all__ = [
    "BackendHTTPClient",
    "BackendHTTPError",
    "BackendSessionFactory",
    "BackendWebSocketClient",
    "BaseBackendClient",
    "get_backend_client",
]
