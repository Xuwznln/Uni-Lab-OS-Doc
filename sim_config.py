# Uni-Lab-OS Edge 本地配置：开启 Isaac Sim 网关
# 用法（在 Uni-Lab-OS/ 目录下）：
#   python -m unilabos.app.main ... --config sim_config.py
# --config 会用本文件中同名 *Config 类的属性覆盖默认配置。


class SimGatewayConfig:
    # 开启网关，Edge 启动时会创建 IsaacSimGateway 并连接下面的 endpoint
    enabled = True
    # Isaac Sim bridge 监听地址（与 bridge 启动日志里的 listening 地址一致）
    endpoint = "ws://127.0.0.1:9000/edge-sim/v1"
    auth_token = ""
    target = "isaac-sim-main"
    world_name = "lab_world_01"
    auto_bootstrap = True
