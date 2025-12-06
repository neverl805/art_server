"""日志管理CLI工具"""
import argparse
from app.database.db import db_manager


def clean_logs(days: int):
    """清理指定天数之前的日志"""
    print(f"🗑️  开始清理 {days} 天前的日志...")
    db_manager.clean_old_logs(days)
    print("✅ 日志清理完成！")


def show_stats():
    """显示日志统计信息"""
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()

        # 总日志数
        cursor.execute("SELECT COUNT(*) as total FROM logs")
        total = cursor.fetchone()['total']

        # 按级别统计
        cursor.execute("""
            SELECT level, COUNT(*) as count
            FROM logs
            GROUP BY level
            ORDER BY count DESC
        """)
        level_stats = cursor.fetchall()

        # 数据库文件大小
        import os
        db_size = os.path.getsize(db_manager.db_path) / (1024 * 1024)  # MB

        print("\n" + "="*50)
        print("📊 日志统计信息")
        print("="*50)
        print(f"数据库文件: {db_manager.db_path}")
        print(f"文件大小: {db_size:.2f} MB")
        print(f"总日志数: {total:,}")
        print("\n级别分布:")
        for row in level_stats:
            print(f"  {row['level']}: {row['count']:,}")
        print("="*50 + "\n")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="日志管理工具")
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # clean命令
    clean_parser = subparsers.add_parser('clean', help='清理旧日志')
    clean_parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='保留最近N天的日志 (默认: 30)'
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
