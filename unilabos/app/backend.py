from __future__ import annotations

import threading

from unilabos.resources.resource_tracker import ResourceTreeSet
from unilabos.sim.runtime import RuntimeServices, configure_runtime
from unilabos.utils import logger


_runtime_services: RuntimeServices | None = None


# 根据选择的 backend 启动相应的功能
def start_backend(
    backend: str,
    devices_config: ResourceTreeSet,
    resources_config: ResourceTreeSet,
    resources_edge_config: list[dict] = [],
    graph=None,
    controllers_config: dict = {},
    bridges=[],
    is_slave: bool = False,
    visual: str = "None",
    resources_mesh_config: dict = {},
    **kwargs,
):
    global _runtime_services
    mode = kwargs.get("mode", "real")
    sim_rate = kwargs.get("sim_rate", 1.0)
    sim_paused = kwargs.get("sim_paused", False)
    start_sim_services = backend == "ros" and not kwargs.get("disable_sim_services", False)
    _runtime_services = configure_runtime(
        mode=mode,
        sim_rate=sim_rate,
        sim_paused=sim_paused,
        start_ros_services=False,
    )
    _runtime_services.context.sim_services_enabled = start_sim_services and mode in ("sim", "twin")
    _runtime_services.context.query_api_enabled = backend == "ros" and not kwargs.get("disable_query_api", False)
    _runtime_services.context.query_grpc_port = int(kwargs.get("query_grpc_port", 50051))
    logger.info(
        "Runtime mode initialized: "
        f"mode={mode}, sim_rate={_runtime_services.context.clock.scale}, "
        f"paused={_runtime_services.context.clock.paused}, "
        f"sim_services={start_sim_services and mode in ('sim', 'twin')}, "
        f"query_api={_runtime_services.context.query_api_enabled}, "
        f"grpc_port={_runtime_services.context.query_grpc_port}"
    )

    if backend == "ros":
        # 假设 ros_main, simple_main, automancer_main 是不同 backend 的启动函数
        from unilabos.ros.main_slave_run import main, slave  # 如果选择 'ros' 作为 backend
    elif backend == "simple":
        # 这里假设 simple_backend 和 automancer_backend 是你定义的其他两个后端
        # from simple_backend import main as simple_main
        pass
    elif backend == "automancer":
        # from automancer_backend import main as automancer_main
        pass
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    backend_thread = threading.Thread(
        target=main if not is_slave else slave,
        args=(
            devices_config,
            resources_config,
            resources_edge_config,
            graph,
            controllers_config,
            bridges,
            visual,
            resources_mesh_config,
        ),
        name="backend_thread",
        daemon=True,
    )
    backend_thread.start()
    logger.info(f"Backend {backend} started.")
