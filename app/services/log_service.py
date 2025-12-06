"""日志服务模块"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
from app.models.log import (
    LogEntry, LogGroup, LogSearchParams,
    LogOverviewStats, LogListResponse, LogLevel
)
from app.utils.log_parser import LogParser


class LogService:
    """日志服务类"""

    def __init__(self, log_file_path: str):
        """
        初始化日志服务

        Args:
            log_file_path: 日志文件路径
        """
        self.log_file_path = log_file_path
        self.parser = LogParser()
        self._cache = {}  # 简单的缓存机制

    def get_all_logs(self, limit: Optional[int] = None) -> List[LogEntry]:
        """
        获取所有日志

        Args:
            limit: 限制条数

        Returns:
            日志列表
        """
        return self.parser.parse_file(self.log_file_path, limit=limit)

    def search_logs(self, params: LogSearchParams) -> LogListResponse:
        """
        搜索日志

        Args:
            params: 搜索参数

        Returns:
            日志列表响应
        """
        # 获取所有日志
        all_logs = self.get_all_logs()

        # 过滤日志
        filtered_logs = self._filter_logs(all_logs, params)

        # 按request_id分组
        log_groups = self._group_by_request_id(filtered_logs)

        # 分页
        start = (params.page - 1) * params.page_size
        end = start + params.page_size
        paginated_groups = log_groups[start:end]

        return LogListResponse(
            total=len(log_groups),
            page=params.page,
            page_size=params.page_size,
            data=paginated_groups
        )

    def get_log_detail(self, request_id: str) -> Optional[LogGroup]:
        """
        获取日志详情

        Args:
            request_id: 请求ID

        Returns:
            日志组
        """
        all_logs = self.get_all_logs()
        request_logs = [log for log in all_logs if log.request_id == request_id]

        if not request_logs:
            return None

        return self._create_log_group(request_id, request_logs)

    def get_overview_stats(self) -> LogOverviewStats:
        """
        获取总览统计

        Returns:
            统计数据
        """
        all_logs = self.get_all_logs()

        if not all_logs:
            return LogOverviewStats(
                total=0,
                error_count=0,
                warning_count=0,
                info_count=0,
                debug_count=0,
                request_count=0,
                ip_count=0,
                level_distribution={},
                timeline_data=[],
                ip_stats=[],
                recent_logs=[]
            )

        # 统计各级别日志数
        level_counts = defaultdict(int)
        for log in all_logs:
            level_counts[log.level.value] += 1

        # 统计request_id数量
        request_ids = set(log.request_id for log in all_logs)

        # 统计IP数量
        ips = set(log.ip for log in all_logs)

        # IP统计
        ip_counts = defaultdict(int)
        for log in all_logs:
            ip_counts[log.ip] += 1

        ip_stats = [
            {"ip": ip, "count": count}
            for ip, count in sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        # 时间线数据 (按小时统计)
        timeline_data = self._generate_timeline_data(all_logs)

        # 最近日志 (取最新20条)
        recent_logs = sorted(all_logs, key=lambda x: x.timestamp, reverse=True)[:20]

        return LogOverviewStats(
            total=len(all_logs),
            error_count=level_counts.get(LogLevel.ERROR.value, 0),
            warning_count=level_counts.get(LogLevel.WARNING.value, 0),
            info_count=level_counts.get(LogLevel.INFO.value, 0),
            debug_count=level_counts.get(LogLevel.DEBUG.value, 0),
            request_count=len(request_ids),
            ip_count=len(ips),
            level_distribution=dict(level_counts),
            timeline_data=timeline_data,
            ip_stats=ip_stats,
            recent_logs=recent_logs
        )

    def _filter_logs(self, logs: List[LogEntry], params: LogSearchParams) -> List[LogEntry]:
        """
        过滤日志

        Args:
            logs: 日志列表
            params: 搜索参数

        Returns:
            过滤后的日志列表
        """
        filtered = logs

        if params.request_id:
            filtered = [log for log in filtered if params.request_id in log.request_id]

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
            filtered = [
                log for log in filtered
                if params.keyword.lower() in log.message.lower()
            ]

        return filtered

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

    def _generate_timeline_data(self, logs: List[LogEntry]) -> List[Dict]:
        """
        生成时间线数据

        Args:
            logs: 日志列表

        Returns:
            时间线数据
        """
        if not logs:
            return []

        # 按小时分组统计
        hourly_counts = defaultdict(lambda: defaultdict(int))

        for log in logs:
            # 将时间戳向下取整到小时
            hour_key = log.timestamp.strftime("%Y-%m-%d %H:00:00")
            hourly_counts[hour_key][log.level.value] += 1

        # 转换为列表格式
        timeline_data = []
        for hour, level_counts in sorted(hourly_counts.items()):
            data_point = {
                "time": hour,
                "total": sum(level_counts.values()),
                **level_counts
            }
            timeline_data.append(data_point)

        return timeline_data
