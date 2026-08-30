"""微后端稳定通信协议（契约层，不依赖 ``unilabos.server``）。

请从具体子模块导入：``base``（DTO 基类与规范 JSON）、``common``（幂等信封）、
``control`` / ``history`` / ``materials`` / ``runtime`` / ``telemetry`` /
``virtual_environment``（各域协议对象）。

本包 ``__init__`` 不做 eager re-export：部分协议模块复用
``unilabos.server.database.tables`` 中的表模型作 DTO，顶层全家桶会在
表模块加载途中制造循环导入。
"""
