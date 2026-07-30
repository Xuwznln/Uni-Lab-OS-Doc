# unilabos的配置文件

class BasicConfig:
    ak = ""  # 实验室网页给您提供的ak代码，您可以在配置文件中指定，也可以通过运行unilabos时以 --ak 传入，优先按照传入参数解析
    sk = ""  # 实验室网页给您提供的sk代码，您可以在配置文件中指定，也可以通过运行unilabos时以 --sk 传入，优先按照传入参数解析


# WebSocket配置，一般无需调整
class WSConfig:
    reconnect_interval = 5  # 重连间隔（秒）
    max_reconnect_attempts = 999  # 最大重连次数
    ws_ping_interval = 5  # ping间隔（秒），对齐服务端 PingPeriod
    ws_ping_timeout = 7  # pong等待超时（秒），对齐服务端 PongWait


# OpenTelemetry/SigNoz 默认关闭。生产环境建议用环境变量注入 endpoint/headers，
# 不要把 token 或认证 header 写进配置文件。
class OTelConfig:
    enabled = False
    endpoint = ""  # OTLP/gRPC，例如 http://signoz-otel-collector:4317
    insecure = True
    service_name = "uni-lab-edge"
    deployment_environment = ""
