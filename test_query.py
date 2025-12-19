"""测试 art_server 查询接口"""
import sys
sys.path.insert(0, '.')

from app.services.log_service_redis import log_service_redis as log_service
from app.models.log import LogSearchParams
from app.database.redis_logger import redis_logger_manager
import config

# 初始化Redis
print("[INFO] 初始化Redis连接...")
redis_logger_manager.initialize(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    password=config.REDIS_PASSWORD,
    db=config.REDIS_DB
)

# 测试 Redis 连接
print("\n[TEST 1] 测试Redis连接:")
if redis_logger_manager.initialized:
    print("  ✅ Redis连接成功")
    # 获取日志数量
    total_count = redis_logger_manager.redis_client.zcard('logs:timeline')
    print(f"  - 日志总数: {total_count}")

    # 获取最近5条日志
    recent_logs = redis_logger_manager.get_recent_logs(5)
    print(f"  - 最近5条日志:")
    for log in recent_logs:
        print(f"    timestamp={log['timestamp']}, level={log['level']}, request_id={log['request_id']}")
else:
    print("  ❌ Redis未初始化")

# 测试 search_logs 方法
print("\n[TEST 2] 测试 search_logs 方法:")
params = LogSearchParams(
    request_id=None,
    level=None,
    ip=None,
    module=None,
    start_time=None,
    end_time=None,
    keyword=None,
    page=1,
    page_size=20
)
result = log_service.search_logs(params)
print(f"  - total: {result.total}")
print(f"  - page: {result.page}")
print(f"  - page_size: {result.page_size}")
print(f"  - data length: {len(result.data)}")
if result.data:
    print(f"  - 第一个分组:")
    first_group = result.data[0]
    print(f"    request_id: {first_group.request_id}")
    print(f"    count: {first_group.count}")
    print(f"    logs: {len(first_group.logs)}")

# 测试 overview
print("\n[TEST 3] 测试 get_overview_stats 方法:")
stats = log_service.get_overview_stats()
print(f"  - total: {stats.total}")
print(f"  - error_count: {stats.error_count}")
print(f"  - warning_count: {stats.warning_count}")
print(f"  - info_count: {stats.info_count}")
print(f"  - request_count: {stats.request_count}")
print(f"  - recent_logs: {len(stats.recent_logs)}")

print("\n[OK] 测试完成")
