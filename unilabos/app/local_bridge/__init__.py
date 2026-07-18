"""本地工作流桥（local_bridge）— 在无 Go 后端环境下替代后端"翻译面"职责。

桥有三面：
- schedule_ws.py：OS 面 WS 服务器（/api/v1/ws/schedule），OS 的 ws_client 主动连入。
- workflow_ws.py：实现 A UI 面 WS 服务器（/ws/workflow/{uuid}），云端两个 panel 连入。
- local_api.py：实现 B UI 面 HTTP 服务器（/api/*），SZLab local_ui 轮询。

三面共享翻译核 workflow_to_dag.py，最终都产出 F002 TaskDag 交同一执行路径。
桥不复制执行逻辑——只翻译协议、把整张图交 F002 真实执行、再翻译回流的 job_status。

契约见 docs/features/F003-local-workflow-bridge/interface-design.md。
"""

from __future__ import annotations
