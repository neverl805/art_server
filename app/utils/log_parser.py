"""日志解析器"""
import re
from datetime import datetime
from typing import Optional, List
from app.models.log import LogEntry, LogLevel


class LogParser:
    """日志解析器类"""

    # 日志格式正则表达式
    # 格式: {ip} {time} [{request_id}] | {level} | {module}.{function}:{line} : {message}
    LOG_PATTERN = re.compile(
        r'^(?P<ip>\S+)\s+'  # IP地址
        r'(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+'  # 时间戳
        r'\[(?P<request_id>[^\]]+)\]\s+\|\s+'  # request_id
        r'(?P<level>\w+)\s+\|\s+'  # 日志级别
        r'(?P<module>[^.]+)\.(?P<function>[^:]+):(?P<line>\d+)\s+:\s+'  # 模块.函数:行号
        r'(?P<message>.*)$'  # 消息
    )

    @classmethod
    def parse_line(cls, line: str, line_number: int = 0) -> Optional[LogEntry]:
        """
        解析单行日志

        Args:
            line: 日志行
            line_number: 行号

        Returns:
            LogEntry对象或None
        """
        line = line.strip()
        if not line:
            return None

        match = cls.LOG_PATTERN.match(line)
        if not match:
            return None

        try:
            data = match.groupdict()

            # 解析时间戳
            timestamp_str = data['timestamp']
            # 处理毫秒部分
            if '.' in timestamp_str:
                # 2025-12-06 01:17:59.1759 格式
                dt_part, ms_part = timestamp_str.split('.')
                # 只取前3位作为毫秒
                ms_part = ms_part[:3].ljust(3, '0')
                timestamp_str = f"{dt_part}.{ms_part}"
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
            else:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")

            # 验证日志级别
            level_str = data['level'].upper()
            try:
                level = LogLevel(level_str)
            except ValueError:
                # 如果不是标准级别，默认为INFO
                level = LogLevel.INFO

            return LogEntry(
                id=line_number,
                ip=data['ip'],
                timestamp=timestamp,
                request_id=data['request_id'],
                level=level,
                module=data['module'],
                function=data['function'],
                line=int(data['line']),
                message=data['message'],
                raw_line=line
            )
        except Exception as e:
            print(f"解析日志行出错: {e}, 行内容: {line}")
            return None

    @classmethod
    def parse_file(cls, file_path: str, limit: Optional[int] = None, offset: int = 0) -> List[LogEntry]:
        """
        解析日志文件

        Args:
            file_path: 文件路径
            limit: 限制读取条数
            offset: 跳过的行数

        Returns:
            日志列表
        """
        logs = []
        line_number = 0

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line_number += 1

                    # 跳过offset之前的行
                    if line_number <= offset:
                        continue

                    # 解析日志
                    log_entry = cls.parse_line(line, line_number)
                    if log_entry:
                        logs.append(log_entry)

                    # 达到限制条数则停止
                    if limit and len(logs) >= limit:
                        break
        except FileNotFoundError:
            print(f"日志文件不存在: {file_path}")
        except Exception as e:
            print(f"读取日志文件出错: {e}")

        return logs

    @classmethod
    def get_file_line_count(cls, file_path: str) -> int:
        """
        获取文件总行数

        Args:
            file_path: 文件路径

        Returns:
            行数
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f)
        except Exception:
            return 0
