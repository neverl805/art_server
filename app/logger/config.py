"""日志配置模块"""
from loguru import logger
import sys
from pathlib import Path
import contextvars
import platform

# 使用 contextvars 来保存请求上下文
request_ip_var = contextvars.ContextVar("request_ip", default="127.0.0.1")
request_id_var = contextvars.ContextVar("request_id", default="SYSTEM")
os_name = platform.system()


def add_context_to_log(record):
    """将 contextvars 中的上下文信息添加到日志记录中（patcher 方式）"""
    record["extra"]["ip"] = request_ip_var.get()
    record["extra"]["request_id"] = request_id_var.get()


def setup_logger():
    """配置loguru日志系统"""

    # 移除默认的handler
    logger.remove()

    # 日志格式
    log_format = (
        "{extra[ip]} "
        "{time:YYYY-MM-DD HH:mm:ss.SSS} "
        "[{extra[request_id]}] | "
        "{level} | "
        "{module}.{function}:{line} : "
        "{message}"
    )

    # 日志目录
    log_dir = Path("logs_backup")
    log_dir.mkdir(exist_ok=True)

    handles = [
        # 1. 文件备份 (保留最近7天，每天一个文件)
        {
            "sink": str(log_dir / "app_{time:YYYY-MM-DD}.log"),
            "format": log_format,
            "level": "INFO",
            "rotation": "10 MB",
            "retention": 5,
            "enqueue": True,
            "encoding": "utf-8",
        },
        # 2. 错误日志单独记录 (保留30天)
        {
            "sink": str(log_dir / "error_{time:YYYY-MM-DD}.log"),
            "format": log_format,
            "level": "ERROR",
            "rotation": "10 MB",
            "retention": 5,
            "enqueue": True,
            "encoding": "utf-8",
        }
    ]

    if os_name == 'Windows':
        # 控制台输出 (带颜色，方便开发调试)
        handles.append({
            "sink": sys.stdout,
            "format": log_format,
            "level": "INFO",
            "colorize": True,
            "enqueue": True,
        })

    # 使用 configure 方法配置 logger，使用 patcher 自动添加上下文
    logger.configure(
        handlers=handles,
        patcher=add_context_to_log,  # 使用 patcher 自动从 contextvars 添加上下文
        extra={"ip": "127.0.0.1", "request_id": "SYSTEM"}  # 默认值
    )

    print("[OK] 日志系统初始化完成")
    print(f"  - 控制台输出: INFO+")
    print(f"  - 文件备份: {log_dir.absolute()} (7天)")
    print(f"  - 错误日志: {log_dir.absolute()} (30天)")
    print(f"  - Redis查询: 从其他服务读取")


# 创建日志上下文管理器
class LoggerContext:
    """日志上下文管理器，用于设置和获取 request_ip 和 request_id"""

    def set_context(self, ip: str = None, request_id: str = None):
        """
        设置当前请求的上下文信息（使用 contextvars）

        Args:
            ip: IP地址
            request_id: 请求ID
        """
        if ip is not None:
            request_ip_var.set(ip)
        if request_id is not None:
            request_id_var.set(request_id)

    def get_context(self):
        """
        获取当前上下文信息

        Returns:
            dict: 包含 ip 和 request_id 的字典
        """
        return {
            'ip': request_ip_var.get(),
            'request_id': request_id_var.get()
        }

    def get_logger(self):
        """
        获取logger实例，会自动从 contextvars 获取上下文（通过 patcher）

        Returns:
            logger实例
        """
        return logger


# 全局logger上下文
log_context = LoggerContext()
