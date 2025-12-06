"""测试日志服务修复"""
from app.logger import setup_logger, log_context
from app.database.db import db_manager
from loguru import logger

print("=" * 60)
print("测试日志服务修复")
print("=" * 60)

# 1. 测试日志配置
print("\n1. 初始化日志系统...")
setup_logger()

# 2. 测试简单日志输出
print("\n2. 测试日志输出...")
logger.info("这是一条INFO日志")
logger.warning("这是一条WARNING日志")
logger.error("这是一条ERROR日志")

# 3. 测试带上下文的日志
print("\n3. 测试带上下文的日志...")
ctx_logger = log_context.get_logger(ip="192.168.1.100", request_id="test-001")
ctx_logger.info("这是带上下文的日志")

# 4. 测试数据库连接
print("\n4. 测试数据库连接...")
print(f"   数据库路径: {db_manager.db_path}")
print(f"   数据库存在: {db_manager.db_path.exists()}")

if db_manager.db_path.exists():
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM logs")
            result = cursor.fetchone()
            print(f"   日志总数: {result['count']}")

            cursor.execute("SELECT * FROM logs LIMIT 1")
            sample = cursor.fetchone()
            if sample:
                print(f"   示例日志: {sample['message'][:50]}...")
    except Exception as e:
        print(f"   ❌ 数据库读取失败: {e}")
else:
    print("   ⚠️  数据库文件不存在，请先运行 hcaptcha 服务生成日志")

print("\n" + "=" * 60)
print("测试完成！")
print("=" * 60)
