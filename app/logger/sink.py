"""Loguru自定义Sink - 写入数据库

注意：此文件已停用
本服务不再将日志写入数据库，仅从 hcaptcha 服务读取日志数据
如需恢复数据库日志功能，请在 config.py 中重新配置

保留此文件仅供参考
"""
import re
from datetime import datetime
from app.database.db import db_manager


class DatabaseSink:
    """数据库日志接收器"""

    # 日志格式解析正则
    # 格式: {ip} {time} [{request_id}] | {level} | {module}.{function}:{line} : {message}
    LOG_PATTERN = re.compile(
        r'^(?P<ip>\S+)\s+'  # IP地址
        r'(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)\s+'  # 时间戳
        r'\[(?P<request_id>[^\]]+)\]\s+\|\s+'  # request_id
        r'(?P<level>\w+)\s+\|\s+'  # 日志级别
        r'(?P<module>[^.]+)\.(?P<function>[^:]+):(?P<line>\d+)\s+:\s+'  # 模块.函数:行号
        r'(?P<message>.*)$'  # 消息
    )

    def __call__(self, message):
        """
        Loguru sink回调函数

        Args:
            message: loguru的Message对象
        """
        try:
            # 获取格式化后的日志文本
            log_text = message.record['message'] if isinstance(message, object) else str(message)

            # 如果message是loguru的记录对象
            if hasattr(message, 'record'):
                record = message.record

                # 从extra中获取信息
                ip = record.get('extra', {}).get('ip', '127.0.0.1')
                request_id = record.get('extra', {}).get('request_id', 'NO_REQUEST_ID')

                # 构建日志数据
                log_data = {
                    'ip': ip,
                    'timestamp': record['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                    'request_id': request_id,
                    'level': record['level'].name,
                    'module': record['module'],
                    'function': record['function'],
                    'line': record['line'],
                    'message': record['message'],
                    'raw_line': str(message)  # 保存原始格式化后的内容
                }

                # 写入数据库
                db_manager.insert_log(log_data)

        except Exception as e:
            # 写入数据库失败时，打印错误（但不影响主程序）
            print(f"⚠️  写入数据库日志失败: {e}")


# 全局数据库sink实例
database_sink = DatabaseSink()
