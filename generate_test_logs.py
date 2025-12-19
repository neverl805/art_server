"""生成测试日志数据（写入本地文件）"""
import random
import time
from app.logger import setup_logger, log_context

# 初始化日志系统
setup_logger()

# 示例IP列表
IPS = ["192.168.1.1", "192.168.1.2", "192.168.1.3", "10.0.0.1", "127.0.0.1"]

# 示例请求ID
REQUEST_IDS = ["req001", "req002", "req003", "req004", "req005"]

# 示例消息
MESSAGES = {
    "INFO": [
        "用户登录成功",
        "数据查询完成",
        "文件上传成功",
        "订单创建成功",
        "邮件发送成功"
    ],
    "DEBUG": [
        "SQL查询: SELECT * FROM users",
        "缓存命中: user_profile_123",
        "函数调用: process_data()",
        "参数验证通过",
        "连接池状态: 5/10"
    ],
    "WARNING": [
        "响应时间较慢: 1200ms",
        "缓存未命中: session_xyz",
        "重试第2次",
        "队列积压: 100条",
        "内存使用率: 85%"
    ],
    "ERROR": [
        "数据库连接失败",
        "文件读取错误: Permission denied",
        "API调用超时",
        "JSON解析失败",
        "网络连接中断"
    ]
}


def generate_logs(count: int = 100):
    """
    生成测试日志

    Args:
        count: 生成日志数量
    """
    print(f"开始生成 {count} 条测试日志...")

    for i in range(count):
        # 随机选择IP和请求ID
        ip = random.choice(IPS)
        request_id = random.choice(REQUEST_IDS)

        # 随机选择日志级别
        level = random.choices(
            ["DEBUG", "INFO", "WARNING", "ERROR"],
            weights=[20, 50, 20, 10]  # DEBUG 20%, INFO 50%, WARNING 20%, ERROR 10%
        )[0]

        # 获取对应级别的消息
        message = random.choice(MESSAGES[level])

        # 设置上下文
        log_context.set_context(ip=ip, request_id=request_id)

        # 获取logger
        logger = log_context.get_logger()

        # 记录日志
        if level == "DEBUG":
            logger.debug(message)
        elif level == "INFO":
            logger.info(message)
        elif level == "WARNING":
            logger.warning(message)
        elif level == "ERROR":
            logger.error(message)

        # 每10条打印一次进度
        if (i + 1) % 10 == 0:
            print(f"已生成 {i + 1}/{count} 条日志")

        # 稍微延迟，避免时间戳完全相同
        time.sleep(0.01)

    print(f"✅ 成功生成 {count} 条测试日志！")
    print(f"📊 日志级别分布:")
    print(f"   DEBUG: ~{int(count * 0.2)} 条")
    print(f"   INFO: ~{int(count * 0.5)} 条")
    print(f"   WARNING: ~{int(count * 0.2)} 条")
    print(f"   ERROR: ~{int(count * 0.1)} 条")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="生成测试日志数据")
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="生成日志数量 (默认: 100)"
    )

    args = parser.parse_args()
    generate_logs(args.count)
