"""应用配置"""
from pathlib import Path
import platform
os_name = platform.system()

if os_name == 'Windows':
    # hcaptcha 日志数据库路径
    HCAPTCHA_LOGS_DB_PATH = Path(r"E:\js_reverse\new_hcaptcha\logs.db")
else:
    HCAPTCHA_LOGS_DB_PATH = Path("/home/Neverland/mobile_hcaptcha/logs.db")

# 如果需要使用相对路径，可以这样配置：
# HCAPTCHA_LOGS_DB_PATH = Path(__file__).parent.parent / "js_reverse" / "new_hcaptcha" / "logs.db"

# API 配置
API_HOST = "0.0.0.0"
API_PORT = 8000

# 日志配置
LOG_LEVEL = "INFO"
LOG_DIR = Path("logs")
