# 日志收集系统使用指南

## 📚 系统架构

新的日志收集系统采用 **数据库 + 文件备份** 的双重存储方案：

```
应用日志
    ↓
Loguru (多sink配置)
    ├─→ SQLite数据库 (主存储，用于查询)
    ├─→ 控制台输出 (开发调试)
    ├─→ 文件备份 (app_YYYY-MM-DD.log，保留7天)
    └─→ 错误日志 (error_YYYY-MM-DD.log，保留30天)
```

## ✨ 主要优势

### 相比文件日志的改进

| 特性 | 旧方案(文件) | 新方案(数据库) |
|------|-------------|---------------|
| **查询性能** | ❌ 慢（全文件扫描） | ✅ 快（索引查询） |
| **多条件过滤** | ❌ 复杂 | ✅ 简单（SQL WHERE） |
| **统计聚合** | ❌ 需要全量读取 | ✅ 高效（SQL聚合） |
| **并发写入** | ❌ 可能丢失 | ✅ 事务保证 |
| **文件管理** | ❌ 多文件切割混乱 | ✅ 单文件，自动清理 |
| **存储占用** | 🟡 中等 | ✅ 小（支持压缩） |

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install loguru
```

### 2. 在应用中使用

#### 方式1: 在FastAPI中自动记录（推荐）

日志中间件会自动捕获请求信息并记录：

```python
from fastapi import FastAPI
from app.logger import setup_logger
from app.middleware import LoggingMiddleware

# 初始化日志系统
setup_logger()

app = FastAPI()
app.add_middleware(LoggingMiddleware)

# 所有请求都会自动记录
```

#### 方式2: 手动记录日志

```python
from app.logger.config import log_context

# 获取logger（自动绑定IP和request_id）
logger = log_context.get_logger(ip="192.168.1.1", request_id="abc123")

# 记录各级别日志
logger.debug("调试信息")
logger.info("一般信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")
```

## 📊 日志存储位置

### 数据库存储
- **位置**: `logs.db`
- **类型**: SQLite数据库
- **用途**: 主存储，用于API查询

### 文件备份
- **位置**: `logs_backup/`
- **文件**:
  - `app_YYYY-MM-DD.log` - 所有日志（保留7天）
  - `error_YYYY-MM-DD.log` - 仅错误日志（保留30天）
- **格式**: 每天一个文件，旧文件自动压缩为`.zip`

## 🛠️ 日志管理命令

### 查看统计信息

```bash
python manage_logs.py stats
```

输出示例：
```
==================================================
📊 日志统计信息
==================================================
数据库文件: logs.db
文件大小: 12.34 MB
总日志数: 50,000

级别分布:
  INFO: 35,000
  DEBUG: 10,000
  WARNING: 3,000
  ERROR: 2,000
==================================================
```

### 清理旧日志

```bash
# 清理30天前的日志（默认）
python manage_logs.py clean

# 清理7天前的日志
python manage_logs.py clean --days 7

# 清理90天前的日志
python manage_logs.py clean --days 90
```

## 🔍 日志查询

### 通过API查询

```bash
# 获取日志总览
curl http://localhost:8000/api/logs/overview

# 获取日志列表
curl "http://localhost:8000/api/logs/list?page=1&page_size=20"

# 按级别过滤
curl "http://localhost:8000/api/logs/list?level=ERROR"

# 按IP过滤
curl "http://localhost:8000/api/logs/list?ip=192.168.1.1"

# 按时间范围过滤
curl "http://localhost:8000/api/logs/list?start_time=2024-12-01%2000:00:00&end_time=2024-12-06%2023:59:59"

# 关键词搜索
curl "http://localhost:8000/api/logs/list?keyword=error"
```

### 直接查询数据库

```bash
sqlite3 logs.db

# 查看最新10条日志
SELECT * FROM logs ORDER BY timestamp DESC LIMIT 10;

# 统计错误数量
SELECT COUNT(*) FROM logs WHERE level='ERROR';

# 按小时统计
SELECT strftime('%Y-%m-%d %H:00:00', timestamp) as hour, COUNT(*)
FROM logs
GROUP BY hour
ORDER BY hour DESC;
```

## 📝 日志格式

### 标准格式

```
<IP> <时间戳> [<请求ID>] | <级别> | <模块>.<函数>:<行号> : <消息>
```

### 示例

```
192.168.1.1 2024-12-06 14:30:45.123 [abc12345] | INFO | main.startup:25 : 应用启动成功
127.0.0.1 2024-12-06 14:31:12.456 [def67890] | ERROR | api.logs:42 : 查询失败: 数据库连接错误
```

## ⚙️ 配置说明

### 日志级别

- `DEBUG` - 调试信息（仅数据库）
- `INFO` - 一般信息（所有sink）
- `WARNING` - 警告信息
- `ERROR` - 错误信息（单独文件）
- `CRITICAL` - 严重错误

### 轮转策略

| Sink | 轮转方式 | 保留期限 | 压缩 |
|------|---------|---------|------|
| 数据库 | 无（手动清理） | 自定义 | - |
| 应用日志文件 | 每天午夜 | 7天 | ✅ ZIP |
| 错误日志文件 | 每天午夜 | 30天 | ✅ ZIP |

### 自定义配置

编辑 `app/logger/config.py`:

```python
# 修改文件保留期限
logger.add(
    log_dir / "app_{time:YYYY-MM-DD}.log",
    retention="30 days",  # 改为30天
    ...
)

# 修改轮转大小
logger.add(
    log_dir / "app.log",
    rotation="100 MB",  # 文件达到100MB时轮转
    ...
)
```

## 🔧 维护建议

### 定期清理

建议设置定时任务，每周清理一次旧日志：

**Linux/Mac (crontab)**:
```bash
# 每周日凌晨3点清理30天前的日志
0 3 * * 0 cd /path/to/project && python manage_logs.py clean --days 30
```

**Windows (任务计划程序)**:
1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：每周
4. 操作：启动程序 `python manage_logs.py clean --days 30`

### 数据库优化

定期执行VACUUM优化数据库（清理命令会自动执行）：

```bash
sqlite3 logs.db "VACUUM;"
```

### 备份建议

重要日志建议定期备份数据库文件：

```bash
# 备份数据库
cp logs.db backups/logs_$(date +%Y%m%d).db

# 或使用SQLite的备份命令
sqlite3 logs.db ".backup backups/logs_$(date +%Y%m%d).db"
```

## 📈 性能优化

### 索引

数据库已创建以下索引：
- `idx_timestamp` - 时间索引（降序）
- `idx_request_id` - 请求ID索引
- `idx_level` - 级别索引
- `idx_ip` - IP索引
- `idx_module` - 模块索引

### 查询建议

1. ✅ **使用索引字段过滤**
   ```python
   # 好 - 使用索引
   WHERE timestamp > '2024-12-01' AND level='ERROR'

   # 差 - 不使用索引
   WHERE message LIKE '%error%'
   ```

2. ✅ **限制返回数量**
   ```python
   # 使用分页，不要一次查询太多
   LIMIT 20 OFFSET 0
   ```

3. ✅ **定期清理旧数据**
   ```bash
   # 保持数据库在合理大小
   python manage_logs.py clean --days 30
   ```

## 🚨 故障排查

### 数据库锁定

如果遇到 "database is locked" 错误：

1. 检查是否有多个进程同时访问
2. 使用 `db_manager.get_connection()` 上下文管理器
3. 启用WAL模式：
   ```bash
   sqlite3 logs.db "PRAGMA journal_mode=WAL;"
   ```

### 日志丢失

1. 检查sink是否正确配置
2. 查看 `database_sink` 是否有错误输出
3. 确认数据库文件权限正确

### 性能问题

1. 检查数据库大小，及时清理旧数据
2. 确认索引是否存在
3. 考虑增加查询缓存

## 📦 依赖说明

```txt
fastapi>=0.100.0
loguru>=0.7.0
uvicorn>=0.23.0
pydantic>=2.0.0
```

## 🎯 最佳实践

1. ✅ **始终使用上下文信息**
   ```python
   logger = log_context.get_logger(ip=client_ip, request_id=req_id)
   logger.info("处理请求")
   ```

2. ✅ **合理选择日志级别**
   - DEBUG: 详细的调试信息
   - INFO: 重要的业务流程
   - WARNING: 可能的问题
   - ERROR: 错误但程序可继续
   - CRITICAL: 严重错误，程序可能崩溃

3. ✅ **记录关键信息**
   ```python
   logger.info(f"用户 {user_id} 执行操作: {action}")
   logger.error(f"数据库查询失败: {error}", exc_info=True)
   ```

4. ✅ **定期维护**
   - 每周清理旧日志
   - 每月检查数据库大小
   - 定期备份重要日志

## 📞 支持

如有问题，请查看：
1. API文档: http://localhost:8000/docs
2. 日志文件: `logs_backup/error_*.log`
3. 数据库: `logs.db`
