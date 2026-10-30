# 开源串行调度动态库

本库实现 FIFO Task 串行推进，同一 Task 内允许依赖和资源合法的并行。
它与商业 CP-SAT 库使用同一六符号 C ABI、协议、独立 worker 和平台执行闸门。
`native/scheduler_shared` 只包含Rust 公共协议、语义校验、生命周期、串行策略和发行校验。
串行库的构建不读取 `unilabos/scheduling/cpsat`，也不需要 OR-Tools。

安装 wheel 后，默认 `queue` 从包内 `queue_native/plugin.json` 装配真实动态库。
源码开发时先显式构建；运行时不会调用编译器或退回 Python 调度算法：

```bash
python native/scheduler-queue/build.py --destination unilabos/scheduling/queue_native
```

也可将库交付到新目录，并用 `UNILABOS_SCHEDULING_MANIFEST` 指定其清单。
目标目录须不存在；重打包不修改已有交付物。构建 wheel 会自动编译并携带串行库，
wheel 标记目标操作系统和 CPU，使用 `py3-none`，不绑定 CPython ABI。
库内没有 Python 解释器；源码构建工具仍需要 Rust、Python 及声明的构建依赖。
worker 在 Windows 使用认证回环通道和 Job Object 回收，在 POSIX 使用进程组。
公开 CI 覆盖 Linux/macOS/Windows；本地执行证据来自 macOS，不使用 Python 算法降级。

公开源码树和便携包统一使用 `scripts/export_public_source.py` 导出。
发行边界与验证命令见 [调度发行说明](../../docs/scheduler/release.md)。

`QueueEngine` 的 Python 类只提供原生字节传输；第三方 Python 算法应继承公共
`JsonEngine`，不通过覆盖内置适配器的方法改变原生策略。
