"""测试 art_server 查询接口"""
import sys
sys.path.insert(0, '.')

from app.services.log_service_db import log_service
from app.models.log import LogSearchParams
from app.database.db import db_manager

print(f"[INFO] 数据库路径: {db_manager.db_path}")

# 测试直接数据库查询
print("\n[TEST 1] 直接查询数据库:")
with db_manager.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM logs")
    count = cursor.fetchone()['count']
    print(f"  - logs 表总记录数: {count}")

    cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    print(f"  - 最新5条记录:")
    for row in rows:
        print(f"    ID={row['id']}, timestamp={row['timestamp']}, level={row['level']}, request_id={row['request_id']}")

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
