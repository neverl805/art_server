"""日志服务模块 - 从Redis读取"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
from app.models.log import (
    LogEntry, LogGroup, LogSearchParams,
    LogOverviewStats, LogListResponse, LogLevel
)
from app.database.redis_logger import redis_logger_manager


class LogServiceRedis:
    """日志服务类 - 基于Redis"""

    def get_all_logs(self, limit: Optional[int] = None) -> List[LogEntry]:
        """
        获取所有日志

        Args:
            limit: 限制条数

        Returns:
            日志列表
        """
        logs_data = redis_logger_manager.get_recent_logs(limit or 1000)
        return [self._dict_to_log_entry(log) for log in logs_data]

    def search_logs(self, params: LogSearchParams) -> LogListResponse:
        """
        搜索日志（先分组再分页）- Redis优化版本

        Args:
            params: 搜索参数

        Returns:
            日志列表响应
        """
        try:
            print(f"[DEBUG] search_logs 开始查询: page={params.page}, page_size={params.page_size}")

            # 获取所有request_id
            request_ids = redis_logger_manager.get_all_request_ids(
                start_time=params.start_time,
                end_time=params.end_time
            )

            print(f"[DEBUG] 获取到 {len(request_ids)} 个request_id")

            if not request_ids:
                print("[DEBUG] 没有找到任何request_id，返回空结果")
                return LogListResponse(
                    total=0,
                    page=params.page,
                    page_size=params.page_size,
                    data=[]
                )

            # 筛选request_id
            filtered_request_ids = []
            for request_id in request_ids:
                # 如果指定了request_id筛选
                if params.request_id and params.request_id not in request_id:
                    continue
                filtered_request_ids.append(request_id)

            print(f"[DEBUG] 筛选后剩余 {len(filtered_request_ids)} 个request_id")

            if not filtered_request_ids:
                return LogListResponse(
                    total=0,
                    page=params.page,
                    page_size=params.page_size,
                    data=[]
                )

            # 分页处理
            total = len(filtered_request_ids)
            offset = (params.page - 1) * params.page_size
            paged_request_ids = filtered_request_ids[offset:offset + params.page_size]

            print(f"[DEBUG] 分页处理: total={total}, offset={offset}, 当前页={len(paged_request_ids)}个")

            # 获取这一页的所有日志
            log_groups = []
            for request_id in paged_request_ids:
                logs_data = redis_logger_manager.get_logs_by_request_id(request_id)

                if not logs_data:
                    print(f"[DEBUG] request_id={request_id} 没有日志数据")
                    continue

                print(f"[DEBUG] request_id={request_id} 有 {len(logs_data)} 条日志")

                # 转换为LogEntry
                logs = [self._dict_to_log_entry(log) for log in logs_data]

                # 应用其他筛选条件
                filtered_logs = self._apply_filters(logs, params)

                if filtered_logs:
                    log_group = self._create_log_group(request_id, filtered_logs)
                    log_groups.append(log_group)

            # 按开始时间倒序排序
            log_groups.sort(key=lambda x: x.start_time, reverse=True)

            print(f"[DEBUG] 最终返回 {len(log_groups)} 个日志组")

            return LogListResponse(
                total=total,
                page=params.page,
                page_size=params.page_size,
                data=log_groups
            )

        except Exception as e:
            print(f"[ERROR] 搜索日志失败: {e}")
            import traceback
            traceback.print_exc()
            return LogListResponse(
                total=0,
                page=params.page,
                page_size=params.page_size,
                data=[]
            )

    def get_log_detail(self, request_id: str) -> Optional[LogGroup]:
        """
        获取日志详情

        Args:
            request_id: 请求ID

        Returns:
            日志组
        """
        logs_data = redis_logger_manager.get_logs_by_request_id(request_id)

        if not logs_data:
            return None

        logs = [self._dict_to_log_entry(log) for log in logs_data]
        return self._create_log_group(request_id, logs)

    def get_overview_stats(self) -> LogOverviewStats:
        """
        获取总览统计

        Returns:
            统计数据
        """
        try:
            # 获取最近的日志用于统计
            recent_logs_data = redis_logger_manager.get_recent_logs(10000)

            if not recent_logs_data:
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

            # 转换为LogEntry
            logs = [self._dict_to_log_entry(log) for log in recent_logs_data]

            # 统计各级别日志数
            level_counts = defaultdict(int)
            for log in logs:
                level_counts[log.level.value] += 1

            # 统计request_id和IP数量
            request_ids = set()
            ips = set()
            for log in logs:
                request_ids.add(log.request_id)
                ips.add(log.ip)

            # IP统计
            ip_counts = defaultdict(int)
            for log in logs:
                ip_counts[log.ip] += 1

            ip_stats = [
                {"ip": ip, "count": count}
                for ip, count in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            ]

            # 时间线数据 (最近24小时)
            timeline_data = self._process_timeline_data(logs)

            # 最近日志 (最新20条)
            recent_logs = logs[:20]

            return LogOverviewStats(
                total=len(logs),
                error_count=level_counts.get('ERROR', 0) + level_counts.get('CRITICAL', 0),
                warning_count=level_counts.get('WARNING', 0),
                info_count=level_counts.get('INFO', 0),
                success_count=level_counts.get('SUCCESS', 0),
                debug_count=level_counts.get('DEBUG', 0),
                request_count=len(request_ids),
                ip_count=len(ips),
                level_distribution=dict(level_counts),
                timeline_data=timeline_data,
                ip_stats=ip_stats,
                recent_logs=recent_logs
            )

        except Exception as e:
            print(f"[ERROR] 获取统计数据失败: {e}")
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

    def _dict_to_log_entry(self, log_dict: dict) -> LogEntry:
        """
        将字典转换为LogEntry

        Args:
            log_dict: 日志数据字典

        Returns:
            LogEntry对象
        """
        return LogEntry(
            id=None,  # Redis存储没有自增ID
            ip=log_dict['ip'],
            timestamp=datetime.strptime(log_dict['timestamp'], '%Y-%m-%d %H:%M:%S.%f'),
            request_id=log_dict['request_id'],
            level=LogLevel(log_dict['level']),
            module=log_dict['module'],
            function=log_dict['function'],
            line=int(log_dict['line']),
            message=log_dict['message'],
            raw_line=log_dict['raw_line']
        )

    def _apply_filters(self, logs: List[LogEntry], params: LogSearchParams) -> List[LogEntry]:
        """
        应用筛选条件

        Args:
            logs: 日志列表
            params: 搜索参数

        Returns:
            筛选后的日志列表
        """
        filtered = logs

        if params.level:
            filtered = [log for log in filtered if log.level == params.level]

        if params.ip:
            filtered = [log for log in filtered if params.ip in log.ip]

        if params.module:
            filtered = [log for log in filtered if params.module in log.module]

        if params.start_time:
            filtered = [log for log in filtered if log.timestamp >= params.start_time]

        if params.end_time:
            filtered = [log for log in filtered if log.timestamp <= params.end_time]

        if params.keyword:
            filtered = [log for log in filtered if params.keyword in log.message]

        return filtered

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

    def _process_timeline_data(self, logs: List[LogEntry]) -> List[Dict]:
        """
        处理时间线数据

        Args:
            logs: 日志列表

        Returns:
            时间线数据列表
        """
        # 筛选最近24小时的日志
        cutoff_time = datetime.now() - timedelta(hours=24)
        recent_logs = [log for log in logs if log.timestamp >= cutoff_time]

        # 按小时分组
        hourly_data = defaultdict(lambda: defaultdict(int))

        for log in recent_logs:
            hour = log.timestamp.strftime('%Y-%m-%d %H:00:00')
            hourly_data[hour][log.level.value] += 1

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
log_service_redis = LogServiceRedis()
