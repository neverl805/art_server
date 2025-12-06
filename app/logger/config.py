"""日志配置模块 - 简化版"""
from loguru import logger
import sys
from pathlib import Path


def setup_logger():
    """配置loguru日志系统 - 简化版，仅控制台输出"""

    # 移除默认的handler
    logger.remove()

    # 简化的日志格式
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # 控制台输出 (带颜色)
    logger.add(
        sys.stdout,
        format=log_format,
        level="INFO",
        colorize=True,
    )

    # 可选：错误日志文件（保留7天）
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.add(
        log_dir / "error_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="ERROR",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8"
    )

    print("[OK] 日志系统初始化完成 (简化版)")
    print(f"  - 控制台输出: INFO+")
    print(f"  - 错误日志: {log_dir.absolute()}")


# 创建带默认值的logger包装器
class LoggerContext:
    """日志上下文管理器"""

    def __init__(self):
        self.default_ip = "127.0.0.1"
        self.default_request_id = "SYSTEM"

    def bind(self, ip: str = None, request_id: str = None):
        """
        绑定上下文信息

        Args:
            ip: IP地址
            request_id: 请求ID

        Returns:
            logger实例
        """
        return logger.bind(
            ip=ip or self.default_ip,
            request_id=request_id or self.default_request_id
        )

    def get_logger(self, **kwargs):
        """
        获取logger实例，支持自动绑定上下文

        Args:
            **kwargs: 上下文键值对

        Returns:
            logger实例
        """
        context = {
            'ip': kwargs.get('ip', self.default_ip),
            'request_id': kwargs.get('request_id', self.default_request_id)
        }
        return logger.bind(**context)


# 全局logger上下文
log_context = LoggerContext()
