"""
日志适配器模块

用于将各种框架的日志（如Uvicorn、FastAPI等）统一适配到ilabos的日志系统
"""

import logging

from unilabos.utils.log import debug, info, warning, error, critical, trace

# 只读请求：前端轮询与 CORS 预检，每秒数十条，不该出现在控制台
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def access_log_func(record: logging.LogRecord):
    """按 HTTP 方法与状态码给 uvicorn 访问日志分级。

    写请求（提交工作流、保存图、人工确认……）才是值得在控制台看到的事件，保持 INFO；
    只读请求降到 TRACE（仍全量落盘）；4xx 说明客户端用错了接口，5xx 是服务端故障。
    uvicorn 访问日志的 args 固定为 ``(client, method, path, http_version, status)``，
    形状不符时按 INFO 原样输出。
    """
    args = record.args
    if not isinstance(args, tuple) or len(args) != 5:
        return info
    method, status = args[1], args[4]
    if not isinstance(status, int):
        return info
    if status >= 500:
        return warning
    if status >= 400:
        return debug
    if str(method).upper() in _READ_METHODS:
        return trace
    return info


class UvicornLogAdapter:
    """Uvicorn日志适配器，将Uvicorn的日志重定向到我们的日志系统"""

    @staticmethod
    def configure():
        """配置Uvicorn的日志系统，使用我们自定义的日志格式"""
        # 获取uvicorn相关的日志记录器
        uvicorn_loggers = [
            logging.getLogger("uvicorn"),
            logging.getLogger("uvicorn.access"),
            logging.getLogger("uvicorn.error"),
            logging.getLogger("fastapi"),
        ]

        # 清除现有处理器
        for logger_instance in uvicorn_loggers:
            for handler in logger_instance.handlers[:]:
                logger_instance.removeHandler(handler)

        # 添加自定义处理器
        adapter_handler = UvicornToIlabosHandler()

        # 为所有uvicorn日志记录器添加处理器
        for logger_instance in uvicorn_loggers:
            logger_instance.addHandler(adapter_handler)
            # 设置日志级别
            logger_instance.setLevel(logging.INFO)
            # 禁止传播到根日志记录器，避免重复输出
            logger_instance.propagate = False


class UvicornToIlabosHandler(logging.Handler):
    """将Uvicorn日志处理为ilabos日志格式的处理器"""

    def __init__(self):
        super().__init__()
        self.level_map = {
            logging.DEBUG: debug,
            logging.INFO: info,
            logging.WARNING: warning,
            logging.ERROR: error,
            logging.CRITICAL: critical,
        }

    def emit(self, record):
        """发送日志记录到ilabos日志系统"""
        try:
            msg = self.format(record)
            if record.name == "uvicorn.access":
                log_func = access_log_func(record)
            else:
                log_func = self.level_map.get(record.levelno, info)
            # 根据日志源添加前缀
            if record.name.startswith("uvicorn"):
                prefix = "[Uvicorn] "
                if record.name == "uvicorn.access":
                    prefix = "[Uvicorn.HTTP] "
                msg = f"{prefix}{msg}"
            elif record.name.startswith("fastapi"):
                msg = f"[FastAPI] {msg}"
            else:
                msg = f"{record.name} {msg}"
            log_func(msg, stack_level=5)
        except Exception:
            self.handleError(record)


def setup_fastapi_logging():
    """设置FastAPI/Uvicorn的日志系统"""
    # 配置Uvicorn的日志
    UvicornLogAdapter.configure()

    # 返回适合uvicorn.run()的日志配置。着色交给我们自己的 ColoredFormatter，
    # uvicorn 若再染色，进程号/URL 里的 ANSI 转义会原样写进日志文件。
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(message)s",
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "unilabos.utils.fastapi.log_adapter.UvicornToIlabosHandler",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO"},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
            "uvicorn.access": {"handlers": ["default"], "level": "INFO"},
            "fastapi": {"handlers": ["default"], "level": "INFO"},
        },
    }
