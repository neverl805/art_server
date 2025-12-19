"""Redis日志存储管理器"""
import json
import redis
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import threading


class RedisLoggerManager:
    """Redis日志管理器 - 单例模式"""

    _instance: Optional['RedisLoggerManager'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.redis_client: Optional[redis.Redis] = None
            self.initialized = False

    def initialize(self, host: str = 'localhost', port: int = 6379, password: Optional[str] = None, db: int = 0):
        """
        初始化Redis连接

        Args:
            host: Redis主机地址
            port: Redis端口
            password: Redis密码
            db: Redis数据库编号
        """
        if self.initialized:
            return

        try:
            self.redis_client = redis.Redis(
                host=host,
                port=port,
                password=password,
                db=db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )
            # 测试连接
            self.redis_client.ping()
            self.initialized = True
            print(f"[OK] Redis日志管理器初始化成功: {host}:{port} DB={db}")
        except Exception as e:
            print(f"[ERROR] Redis连接失败: {e}")
            raise

    def insert_log(self, log_data: dict):
        """
        插入单条日志到Redis (优化版)

        存储结构:
        - logs:timeline - 有序集合,按时间戳排序存储所有日志ID
        - logs:request:{request_id} - 列表,存储特定请求的所有日志ID
        - logs:detail:{log_id} - 哈希,存储日志详情
        - logs:by_level:{level} - 有序集合,按日志级别索引
        - logs:by_ip:{ip} - 有序集合,按IP地址索引
        - logs:by_module:{module} - 有序集合,按模块索引

        【新增优化】:
        - logs:requests:index - 有序集合,按最新日志时间排序的request_id列表
        - logs:request:summary:{request_id} - 哈希,存储请求摘要(count, start_time, end_time, has_error, levels, ip)
        - logs:stats:global - 哈希,存储全局统计信息

        Args:
            log_data: 日志数据字典
        """
        if not self.initialized or not self.redis_client:
            print("[WARNING] Redis未初始化,跳过日志写入")
            return

        try:
            # 生成唯一日志ID（使用:::作为分隔符，避免与timestamp中的:冲突）
            # 格式: "2025-12-19 12:00:00.123:::uuid-string:::100"
            log_id = f"{log_data['timestamp']}:::{log_data['request_id']}:::{log_data['line']}"
            timestamp = datetime.strptime(log_data['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
            timestamp_score = timestamp.timestamp()
            request_id = log_data['request_id']
            level = log_data['level']

            # 使用pipeline批量操作
            pipe = self.redis_client.pipeline()

            # 1. 存储日志详情 (Hash)
            pipe.hset(f"logs:detail:{log_id}", mapping={
                'ip': log_data['ip'],
                'timestamp': log_data['timestamp'],
                'request_id': request_id,
                'level': level,
                'module': log_data['module'],
                'function': log_data['function'],
                'line': str(log_data['line']),
                'message': log_data['message'],
                'raw_line': log_data['raw_line']
            })
            pipe.expire(f"logs:detail:{log_id}", 30 * 24 * 60 * 60)

            # 2. 添加到时间线索引 (Sorted Set)
            pipe.zadd('logs:timeline', {log_id: timestamp_score})

            # 3. 添加到请求ID索引 (List + Sorted Set)
            pipe.lpush(f"logs:request:{request_id}", log_id)
            pipe.expire(f"logs:request:{request_id}", 30 * 24 * 60 * 60)
            pipe.zadd(f"logs:request_timeline:{request_id}", {log_id: timestamp_score})
            pipe.expire(f"logs:request_timeline:{request_id}", 30 * 24 * 60 * 60)

            # 4. 添加到日志级别索引 (Sorted Set)
            pipe.zadd(f"logs:by_level:{level}", {log_id: timestamp_score})

            # 5. 添加到IP地址索引 (Sorted Set)
            pipe.zadd(f"logs:by_ip:{log_data['ip']}", {log_id: timestamp_score})

            # 6. 添加到模块索引 (Sorted Set)
            pipe.zadd(f"logs:by_module:{log_data['module']}", {log_id: timestamp_score})

            # ===== 【优化】添加摘要和统计索引 =====

            # 7. 更新request_id索引 (按最新日志时间排序)
            pipe.zadd('logs:requests:index', {request_id: timestamp_score})

            # 8. 更新请求摘要信息
            summary_key = f"logs:request:summary:{request_id}"
            # 检查是否是该请求的第一条日志（使用EXISTS）
            # 注意：pipeline中的命令不会立即执行，这里需要先执行一次查询
            existing_summary = self.redis_client.hgetall(summary_key)

            if not existing_summary:
                # 新请求，初始化摘要
                pipe.hset(summary_key, mapping={
                    'count': '1',
                    'start_time': log_data['timestamp'],
                    'end_time': log_data['timestamp'],
                    'has_error': '1' if level in ['ERROR', 'CRITICAL'] else '0',
                    f'level:{level}': '1',
                    'ip': log_data['ip']
                })
            else:
                # 更新现有摘要
                pipe.hincrby(summary_key, 'count', 1)
                pipe.hset(summary_key, 'end_time', log_data['timestamp'])
                if level in ['ERROR', 'CRITICAL']:
                    pipe.hset(summary_key, 'has_error', '1')
                pipe.hincrby(summary_key, f'level:{level}', 1)

            pipe.expire(summary_key, 30 * 24 * 60 * 60)

            # 9. 更新全局统计
            stats_key = 'logs:stats:global'
            pipe.hincrby(stats_key, 'total', 1)
            pipe.hincrby(stats_key, f'level:{level}', 1)
            # 使用SET存储唯一的request_id和ip
            pipe.sadd('logs:stats:unique_requests', request_id)
            pipe.sadd('logs:stats:unique_ips', log_data['ip'])

            # 执行所有操作
            pipe.execute()

        except Exception as e:
            print(f"[WARNING] Redis写入日志失败: {e}")

    def insert_logs_batch(self, logs_data: List[dict]):
        """
        批量插入日志

        Args:
            logs_data: 日志数据列表
        """
        if not logs_data or not self.initialized:
            return

        try:
            pipe = self.redis_client.pipeline()

            for log_data in logs_data:
                # 使用:::作为分隔符，避免与timestamp中的:冲突
                # 格式: "2025-12-19 12:00:00.123:::uuid-string:::100"
                log_id = f"{log_data['timestamp']}:::{log_data['request_id']}:::{log_data['line']}"
                timestamp = datetime.strptime(log_data['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
                timestamp_score = timestamp.timestamp()

                # 存储日志详情
                pipe.hset(f"logs:detail:{log_id}", mapping={
                    'ip': log_data['ip'],
                    'timestamp': log_data['timestamp'],
                    'request_id': log_data['request_id'],
                    'level': log_data['level'],
                    'module': log_data['module'],
                    'function': log_data['function'],
                    'line': str(log_data['line']),
                    'message': log_data['message'],
                    'raw_line': log_data['raw_line']
                })
                pipe.expire(f"logs:detail:{log_id}", 30 * 24 * 60 * 60)

                # 添加索引
                pipe.zadd('logs:timeline', {log_id: timestamp_score})
                pipe.lpush(f"logs:request:{log_data['request_id']}", log_id)
                pipe.zadd(f"logs:by_level:{log_data['level']}", {log_id: timestamp_score})
                pipe.zadd(f"logs:by_ip:{log_data['ip']}", {log_id: timestamp_score})

            pipe.execute()

        except Exception as e:
            print(f"[WARNING] Redis批量写入日志失败: {e}")

    def get_logs_by_request_id(self, request_id: str, limit: int = 100) -> List[dict]:
        """
        根据request_id获取日志

        Args:
            request_id: 请求ID
            limit: 返回数量限制

        Returns:
            日志列表
        """
        if not self.initialized:
            return []

        try:
            # 获取日志ID列表
            log_ids = self.redis_client.lrange(f"logs:request:{request_id}", 0, limit - 1)

            print(f"[DEBUG] logs:request:{request_id} 有 {len(log_ids)} 个日志ID")

            # 批量获取日志详情
            if not log_ids:
                # 尝试备用方法：从timeline中搜索该request_id的日志
                print(f"[DEBUG] logs:request:{request_id} 为空，尝试从timeline搜索")
                return self._get_logs_from_timeline(request_id, limit)

            pipe = self.redis_client.pipeline()
            for log_id in log_ids:
                pipe.hgetall(f"logs:detail:{log_id}")

            results = pipe.execute()
            valid_logs = [log for log in results if log]

            print(f"[DEBUG] 从 {len(log_ids)} 个ID中获取到 {len(valid_logs)} 条有效日志")

            return valid_logs

        except Exception as e:
            print(f"[ERROR] Redis读取日志失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _get_logs_from_timeline(self, request_id: str, limit: int = 100) -> List[dict]:
        """
        从timeline中搜索特定request_id的日志（备用方法）

        Args:
            request_id: 请求ID
            limit: 返回数量限制

        Returns:
            日志列表
        """
        try:
            # 获取所有日志ID
            all_log_ids = self.redis_client.zrevrange('logs:timeline', 0, -1)

            # 筛选出包含该request_id的日志ID
            matching_log_ids = []
            for log_id in all_log_ids:
                # log_id格式: timestamp:::request_id:::line (新格式使用:::分隔符)
                if f":::{request_id}:::" in log_id:
                    matching_log_ids.append(log_id)
                    if len(matching_log_ids) >= limit:
                        break

            print(f"[DEBUG] 从timeline中找到 {len(matching_log_ids)} 条匹配的日志")

            if not matching_log_ids:
                return []

            # 批量获取日志详情
            pipe = self.redis_client.pipeline()
            for log_id in matching_log_ids:
                pipe.hgetall(f"logs:detail:{log_id}")

            results = pipe.execute()
            return [log for log in results if log]

        except Exception as e:
            print(f"[ERROR] 从timeline搜索日志失败: {e}")
            return []

    def get_recent_logs(self, limit: int = 100) -> List[dict]:
        """
        获取最近的日志

        Args:
            limit: 返回数量限制

        Returns:
            日志列表
        """
        if not self.initialized:
            return []

        try:
            # 从时间线获取最新的日志ID (倒序)
            log_ids = self.redis_client.zrevrange('logs:timeline', 0, limit - 1)

            if not log_ids:
                return []

            # 批量获取日志详情
            pipe = self.redis_client.pipeline()
            for log_id in log_ids:
                pipe.hgetall(f"logs:detail:{log_id}")

            results = pipe.execute()
            return [log for log in results if log]

        except Exception as e:
            print(f"[WARNING] Redis读取日志失败: {e}")
            return []

    def get_all_request_ids(self, start_time: Optional[datetime] = None,
                           end_time: Optional[datetime] = None) -> List[str]:
        """
        获取所有request_id列表（优化版 - 使用索引）

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            request_id列表（按时间倒序）
        """
        if not self.initialized:
            print("[WARNING] Redis未初始化")
            return []

        try:
            # 检查优化索引是否存在
            if self.redis_client.exists('logs:requests:index'):
                # 使用优化后的索引 logs:requests:index (按最新日志时间排序)
                min_score = start_time.timestamp() if start_time else '-inf'
                max_score = end_time.timestamp() if end_time else '+inf'

                # 直接从索引获取request_id（倒序）
                request_ids = self.redis_client.zrevrangebyscore(
                    'logs:requests:index',
                    max_score,
                    min_score
                )

                print(f"[DEBUG] 从logs:requests:index获取到 {len(request_ids)} 个request_id")
                return request_ids
            else:
                # 索引不存在，使用旧方法
                print("[WARNING] logs:requests:index不存在，使用legacy方法")
                return self._get_request_ids_legacy(start_time, end_time)

        except Exception as e:
            print(f"[ERROR] Redis读取request_id失败: {e}")
            import traceback
            traceback.print_exc()
            # 降级：使用旧方法
            return self._get_request_ids_legacy(start_time, end_time)

    def _get_request_ids_legacy(self, start_time: Optional[datetime] = None,
                                end_time: Optional[datetime] = None) -> List[str]:
        """
        获取所有request_id列表（旧方法 - 从timeline解析）

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            request_id列表
        """
        try:
            # 从时间线获取日志ID
            min_score = start_time.timestamp() if start_time else '-inf'
            max_score = end_time.timestamp() if end_time else '+inf'

            log_ids = self.redis_client.zrevrangebyscore(
                'logs:timeline',
                max_score,
                min_score
            )

            print(f"[DEBUG] 从logs:timeline获取到 {len(log_ids)} 条日志")

            # 提取唯一的request_id
            request_ids_set = set()
            for log_id in log_ids:
                # log_id格式: timestamp:::request_id:::line (新格式)
                # 兼容旧格式: timestamp:request_id:line (但旧格式有bug,因为timestamp包含冒号)
                if ':::' in log_id:
                    # 新格式：使用:::分隔符
                    parts = log_id.split(':::', 2)
                    if len(parts) >= 2:
                        request_ids_set.add(parts[1])
                else:
                    # 旧格式（有问题的格式）：尝试解析但可能不准确
                    # 跳过旧格式数据，或者记录警告
                    print(f"[WARNING] 发现旧格式log_id（可能解析错误）: {log_id[:50]}...")
                    continue

            request_ids = list(request_ids_set)
            print(f"[DEBUG] 提取到 {len(request_ids)} 个唯一request_id")
            return request_ids

        except Exception as e:
            print(f"[ERROR] Redis读取request_id失败(legacy): {e}")
            import traceback
            traceback.print_exc()
            return []

    def clean_old_logs(self, days: int = 30):
        """
        清理指定天数之前的日志

        Args:
            days: 保留天数
        """
        if not self.initialized:
            return

        try:
            # 计算截止时间戳
            cutoff_time = datetime.now() - timedelta(days=days)
            cutoff_timestamp = cutoff_time.timestamp()

            # 获取所有过期的日志ID
            expired_log_ids = self.redis_client.zrangebyscore(
                'logs:timeline',
                '-inf',
                cutoff_timestamp
            )

            if not expired_log_ids:
                print(f"[INFO] 没有需要清理的日志")
                return

            # 批量删除
            pipe = self.redis_client.pipeline()

            for log_id in expired_log_ids:
                pipe.delete(f"logs:detail:{log_id}")

            # 从时间线中移除
            pipe.zremrangebyscore('logs:timeline', '-inf', cutoff_timestamp)

            pipe.execute()

            print(f"[INFO] 清理了 {len(expired_log_ids)} 条 {days} 天前的日志")

        except Exception as e:
            print(f"[WARNING] 清理日志失败: {e}")

    def close(self):
        """关闭Redis连接"""
        if self.redis_client:
            self.redis_client.close()
            self.initialized = False
            print("[OK] Redis日志管理器已关闭")


# 全局Redis日志管理器实例
redis_logger_manager = RedisLoggerManager()
