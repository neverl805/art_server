"""日志管理CLI工具 - Redis版本"""
import argparse
from app.database.redis_logger import redis_logger_manager
import config


def clean_logs(days: int):
    """清理指定天数之前的日志"""
    print(f"🗑️  开始清理 {days} 天前的日志...")

    # 初始化Redis
    redis_logger_manager.initialize(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=config.REDIS_PASSWORD,
        db=config.REDIS_DB
    )

    if days == 0:
        # 清除所有日志
        total_count = redis_logger_manager.redis_client.zcard('logs:timeline')
        redis_logger_manager.redis_client.flushdb()
        print(f"✅ 已清除所有日志，共 {total_count} 条")
    else:
        # 清除N天前的日志
        redis_logger_manager.clean_old_logs(days)
        print("✅ 日志清理完成！")


def show_stats():
    """显示日志统计信息"""
    # 初始化Redis
    redis_logger_manager.initialize(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        password=config.REDIS_PASSWORD,
        db=config.REDIS_DB
    )

    if not redis_logger_manager.initialized:
        print("❌ Redis未初始化")
        return

    # 总日志数
    total = redis_logger_manager.redis_client.zcard('logs:timeline')

    # 获取最近的日志统计级别分布
    recent_logs = redis_logger_manager.get_recent_logs(10000)
    level_counts = {}
    request_ids = set()
    ips = set()

    for log in recent_logs:
        level = log.get('level', 'UNKNOWN')
        level_counts[level] = level_counts.get(level, 0) + 1
        request_ids.add(log.get('request_id', ''))
        ips.add(log.get('ip', ''))

    # Redis内存使用
    info = redis_logger_manager.redis_client.info('memory')
    memory_mb = info['used_memory'] / (1024 * 1024)

    print("\n" + "="*50)
    print("📊 日志统计信息（Redis）")
    print("="*50)
    print(f"Redis服务器: {config.REDIS_HOST}:{config.REDIS_PORT}")
    print(f"内存使用: {memory_mb:.2f} MB")
    print(f"总日志数: {total:,}")
    print(f"请求数: {len(request_ids):,}")
    print(f"IP数: {len(ips):,}")
    print("\n级别分布:")
    for level, count in sorted(level_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {level}: {count:,}")
    print("="*50 + "\n")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="日志管理工具（Redis版本）")
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # clean命令
    clean_parser = subparsers.add_parser('clean', help='清理旧日志')
    clean_parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='保留最近N天的日志 (默认: 30, 设置为0清除所有日志)'
    )

    # stats命令
    subparsers.add_parser('stats', help='查看日志统计')

    args = parser.parse_args()

    if args.command == 'clean':
        clean_logs(args.days)
    elif args.command == 'stats':
        show_stats()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
