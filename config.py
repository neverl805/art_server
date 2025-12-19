"""应用配置"""
from pathlib import Path
import platform

os_name = platform.system()

# API 配置
API_HOST = "0.0.0.0"
API_PORT = 8000

# 日志配置
LOG_LEVEL = "INFO"
LOG_DIR = Path("logs")

# Redis 配置
if os_name == "Windows":
    REDIS_HOST = "103.73.160.204"
else:
    REDIS_HOST = "localhost"

REDIS_PORT = 6379
REDIS_PASSWORD = 'xujiarong250'  # 如果有密码，在这里设置
REDIS_DB = 1  # 日志存储使用的数据库编号
