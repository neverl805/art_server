"""日志服务模块 - 从数据库读取"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
from app.models.log import (
    LogEntry, LogGroup, LogSearchParams,
    LogOverviewStats, LogListResponse, LogLevel
)
from app.database.db import db_manager


class LogService:
    """日志服务类 - 基于数据库"""

    def get_all_logs(self, limit: Optional[int] = None) -> List[LogEntry]:
        """
        获取所有日志

        Args:
            limit: 限制条数

        Returns:
            日志列表
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM logs ORDER BY timestamp DESC"
            if limit:
                query += f" LIMIT {limit}"

            cursor.execute(query)
            rows = cursor.fetchall()

            return [self._row_to_log_entry(row) for row in rows]

    def search_logs(self, params: LogSearchParams) -> LogListResponse:
        """
        搜索日志

        Args:
            params: 搜索参数

        Returns:
            日志列表响应
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # 构建查询条件
            where_clauses = []
            query_params = []

            if params.request_id:
                where_clauses.append("request_id LIKE ?")
                query_params.append(f"%{params.request_id}%")

            if params.level:
                where_clauses.append("level = ?")
                query_params.append(params.level.value)

            if params.ip:
                where_clauses.append("ip LIKE ?")
                query_params.append(f"%{params.ip}%")

            if params.module:
                where_clauses.append("module LIKE ?")
                query_params.append(f"%{params.module}%")

            if params.start_time:
                where_clauses.append("timestamp >= ?")
                query_params.append(params.start_time.strftime('%Y-%m-%d %H:%M:%S'))

            if params.end_time:
                where_clauses.append("timestamp <= ?")
                query_params.append(params.end_time.strftime('%Y-%m-%d %H:%M:%S'))

            if params.keyword:
                where_clauses.append("message LIKE ?")
                query_params.append(f"%{params.keyword}%")

            # 组装SQL
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            # 获取总数
            count_query = f"SELECT COUNT(*) as total FROM logs WHERE {where_sql}"
            cursor.execute(count_query, query_params)
            total = cursor.fetchone()['total']

            # 获取数据
            data_query = f"""
                SELECT * FROM logs
                WHERE {where_sql}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            offset = (params.page - 1) * params.page_size
            cursor.execute(data_query, query_params + [params.page_size, offset])
            rows = cursor.fetchall()

            # 转换为LogEntry
            logs = [self._row_to_log_entry(row) for row in rows]

            # 按request_id分组
            log_groups = self._group_by_request_id(logs)

            return LogListResponse(
                total=total,
                page=params.page,
                page_size=params.page_size,
                data=log_groups
            )

    def get_log_detail(self, request_id: str) -> Optional[LogGroup]:
        """
        获取日志详情

        Args:
            request_id: 请求ID

        Returns:
            日志组
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM logs
                WHERE request_id = ?
                ORDER BY timestamp ASC
            """, (request_id,))

            rows = cursor.fetchall()

            if not rows:
                return None

            logs = [self._row_to_log_entry(row) for row in rows]
            return self._create_log_group(request_id, logs)

    def get_overview_stats(self) -> LogOverviewStats:
        """
        获取总览统计

        Returns:
            统计数据
        """
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # 获取总数
            cursor.execute("SELECT COUNT(*) as total FROM logs")
            total = cursor.fetchone()['total']

            if total == 0:
                return LogOverviewStats(
                    total=0,
                    error_count=0,
                    warning_count=0,
                    info_count=0,
                    success_count=0,
                    debug_count=0,
                    request_count=0,
                    ip_count=0,
                    level_distribution={},
                    timeline_data=[],
                    ip_stats=[],
                    recent_logs=[]
                )

            # 统计各级别日志数
            cursor.execute("""
                SELECT level, COUNT(*) as count
                FROM logs
                GROUP BY level
            """)
            level_counts = {row['level']: row['count'] for row in cursor.fetchall()}

            # 统计request_id数量
            cursor.execute("SELECT COUNT(DISTINCT request_id) as count FROM logs")
            request_count = cursor.fetchone()['count']

            # 统计IP数量
            cursor.execute("SELECT COUNT(DISTINCT ip) as count FROM logs")
            ip_count = cursor.fetchone()['count']

            # IP统计 (Top 10)
            cursor.execute("""
                SELECT ip, COUNT(*) as count
                FROM logs
                GROUP BY ip
                ORDER BY count DESC
                LIMIT 10
            """)
            ip_stats = [{"ip": row['ip'], "count": row['count']} for row in cursor.fetchall()]

            # 时间线数据 (最近24小时，按小时统计)
            cursor.execute("""
                SELECT
                    strftime('%Y-%m-%d %H:00:00', timestamp) as hour,
                    level,
                    COUNT(*) as count
                FROM logs
                WHERE timestamp >= datetime('now', '-24 hours')
                GROUP BY hour, level
                ORDER BY hour
            """)
            timeline_rows = cursor.fetchall()
            timeline_data = self._process_timeline_data(timeline_rows)

            # 最近日志 (最新20条)
            cursor.execute("""
                SELECT * FROM logs
                ORDER BY timestamp DESC
                LIMIT 20
            """)
            recent_logs = [self._row_to_log_entry(row) for row in cursor.fetchall()]

            return LogOverviewStats(
                total=total,
                error_count=level_counts.get('ERROR', 0) + level_counts.get('CRITICAL', 0),
                warning_count=level_counts.get('WARNING', 0),
                info_count=level_counts.get('INFO', 0),
                success_count=level_counts.get('SUCCESS', 0),
                debug_count=level_counts.get('DEBUG', 0),
                request_count=request_count,
                ip_count=ip_count,
                level_distribution=level_counts,
                timeline_data=timeline_data,
                ip_stats=ip_stats,
                recent_logs=recent_logs
            )

    def _row_to_log_entry(self, row) -> LogEntry:
        """
        将数据库行转换为LogEntry

        Args:
            row: 数据库行

        Returns:
            LogEntry对象
        """
        return LogEntry(
            id=row['id'],
            ip=row['ip'],
            timestamp=datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S.%f'),
            request_id=row['request_id'],
            level=LogLevel(row['level']),
            module=row['module'],
            function=row['function'],
            line=row['line'],
            message=row['message'],
            raw_line=row['raw_line']
        )

    def _group_by_request_id(self, logs: List[LogEntry]) -> List[LogGroup]:
        """
        按request_id分组

        Args:
            logs: 日志列表

        Returns:
            日志分组列表
        """
        groups_dict = defaultdict(list)

        for log in logs:
            groups_dict[log.request_id].append(log)

        # 创建LogGroup对象
        groups = []
        for request_id, log_list in groups_dict.items():
            group = self._create_log_group(request_id, log_list)
            groups.append(group)

        # 按开始时间倒序排序
        groups.sort(key=lambda x: x.start_time, reverse=True)

        return groups

    def _create_log_group(self, request_id: str, logs: List[LogEntry]) -> LogGroup:
        """
        创建日志分组

        Args:
            request_id: 请求ID
            logs: 日志列表

        Returns:
            日志分组
        """
        # 按时间排序
        sorted_logs = sorted(logs, key=lambda x: x.timestamp)

        # 统计各级别数量
        level_counts = defaultdict(int)
        for log in sorted_logs:
            level_counts[log.level.value] += 1

        # 计算持续时间
        start_time = sorted_logs[0].timestamp
        end_time = sorted_logs[-1].timestamp
        duration = (end_time - start_time).total_seconds() * 1000  # 转换为毫秒

        # 检查是否有错误
        has_error = any(
            log.level in [LogLevel.ERROR, LogLevel.CRITICAL]
            for log in sorted_logs
        )

        return LogGroup(
            request_id=request_id,
            count=len(sorted_logs),
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration,
            levels=dict(level_counts),
            ip=sorted_logs[0].ip,
            has_error=has_error,
            logs=sorted_logs
        )

    def _process_timeline_data(self, rows) -> List[Dict]:
        """
        处理时间线数据

        Args:
            rows: 数据库查询结果

        Returns:
            时间线数据列表
        """
        # 按小时分组
        hourly_data = defaultdict(dict)

        for row in rows:
            hour = row['hour']
            level = row['level']
            count = row['count']
            hourly_data[hour][level] = count

        # 转换为列表格式
        timeline_data = []
        for hour, level_counts in sorted(hourly_data.items()):
            data_point = {
                "time": hour,
                "total": sum(level_counts.values()),
                **level_counts
            }
            timeline_data.append(data_point)

        return timeline_data


# 全局日志服务实例
log_service = LogService()
