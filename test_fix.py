"""测试日志服务 - 本地文件版"""
from app.logger import setup_logger, log_context
from loguru import logger

print("=" * 60)
print("测试日志服务（本地文件版）")
print("=" * 60)

# 1. 初始化日志系统
print("\n1. 初始化日志系统...")
setup_logger()

# 2. 测试简单日志输出
print("\n2. 测试日志输出...")
logger.info("这是一条INFO日志")
logger.warning("这是一条WARNING日志")
logger.error("这是一条ERROR日志")

# 3. 测试带上下文的日志
print("\n3. 测试带上下文的日志...")
log_context.set_context(ip="192.168.1.100", request_id="test-001")
logger.info("这是带上下文的日志")

# 4. 检查日志文件
print("\n4. 检查日志文件...")
from pathlib import Path
log_dir = Path("logs_backup")
if log_dir.exists():
    print(f"   ✅ 日志目录存在: {log_dir.absolute()}")
    log_files = list(log_dir.glob("*.log"))
    print(f"   日志文件数: {len(log_files)}")
    for f in log_files[:3]:
        print(f"     - {f.name}")
else:
    print("   ⚠️  日志目录不存在")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
