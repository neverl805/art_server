"""SQLite数据库配置"""
import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
import threading
from config import HCAPTCHA_LOGS_DB_PATH


class DatabaseManager:
    """数据库管理器 - 单例模式"""

    _instance: Optional['DatabaseManager'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            # 从配置文件读取 hcaptcha 服务的 logs.db 路径
            self.db_path = HCAPTCHA_LOGS_DB_PATH
            self.initialized = True
            # 验证数据库是否存在
            if not self.db_path.exists():
                print(f"[WARNING] 数据库文件不存在: {self.db_path}")
            else:
                print(f"[OK] 连接到日志数据库: {self.db_path}")
                # 只读模式，不创建表
                self._verify_database()

    def _verify_database(self):
        """验证数据库表结构是否存在"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # 检查logs表是否存在
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='logs'
                """)
                if cursor.fetchone():
                    print(f"  - 数据库表验证成功")
                else:
                    print(f"[WARNING] logs表不存在")
        except Exception as e:
            print(f"[ERROR] 数据库验证失败: {e}")

    def _init_database(self):
        """初始化数据库和表结构"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 创建日志表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT NOT NULL,
                    timestamp DATETIME NOT NULL,
                    request_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    module TEXT NOT NULL,
                    function TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    raw_line TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 创建索引以提升查询性能
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON logs(timestamp DESC)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_request_id
                ON logs(request_id)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_level
                ON logs(level)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_ip
                ON logs(ip)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_module
                ON logs(module)
            ''')

            conn.commit()
            print(f"[OK] 数据库初始化成功: {self.db_path.absolute()}")

    @contextmanager
    def get_connection(self):
        """获取数据库连接 (上下文管理器)"""
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0,  # 增加超时时间到30秒
            isolation_level='DEFERRED'  # 延迟事务，支持读写并发
        )
        # 启用 WAL 模式，与写入端保持一致
        conn.execute('PRAGMA journal_mode=WAL')
        # 设置同步模式
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.row_factory = sqlite3.Row  # 返回字典格式的行
        try:
            yield conn
        finally:
            conn.close()

    def insert_log(self, log_data: dict):
        """
        插入单条日志

        Args:
            log_data: 日志数据字典
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO logs (ip, timestamp, request_id, level, module, function, line, message, raw_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                log_data['ip'],
                log_data['timestamp'],
                log_data['request_id'],
                log_data['level'],
                log_data['module'],
                log_data['function'],
                log_data['line'],
                log_data['message'],
                log_data['raw_line']
            ))
            conn.commit()

    def insert_logs_batch(self, logs_data: list):
        """
        批量插入日志

        Args:
            logs_data: 日志数据列表
        """
        if not logs_data:
            return

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT INTO logs (ip, timestamp, request_id, level, module, function, line, message, raw_line)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                (
                    log['ip'],
                    log['timestamp'],
                    log['request_id'],
                    log['level'],
                    log['module'],
                    log['function'],
                    log['line'],
                    log['message'],
                    log['raw_line']
                )
                for log in logs_data
            ])
            conn.commit()

    def clean_old_logs(self, days: int = 30):
        """
        清理指定天数之前的日志

        Args:
            days: 保留天数
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM logs
                WHERE timestamp < datetime('now', '-' || ? || ' days')
            ''', (days,))
            deleted_count = cursor.rowcount
            conn.commit()
            print(f"[INFO] 清理了 {deleted_count} 条 {days} 天前的日志")

            # 执行VACUUM优化数据库文件大小
            cursor.execute('VACUUM')
            conn.commit()
            print("[OK] 数据库已优化")


# 全局数据库管理器实例
db_manager = DatabaseManager()
