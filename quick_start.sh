#!/bin/bash

echo "================================"
echo "   日志系统快速启动脚本"
echo "================================"
echo ""

# 1. 生成测试日志
echo "🔨 生成测试日志数据..."
python generate_test_logs.py --count 200
echo ""

# 2. 查看日志统计
echo "📊 查看日志统计..."
python manage_logs.py stats
echo ""

# 3. 启动服务器
echo "🚀 启动FastAPI服务器..."
echo "访问 http://localhost:8000/docs 查看API文档"
echo "访问前端页面查看日志可视化界面"
echo ""
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
