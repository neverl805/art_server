#!/bin/bash

echo "========================================"
echo "  日志可视化分析系统 - 启动脚本"
echo "========================================"
echo ""

echo "[1/2] 启动后端服务 (FastAPI)..."
echo "后端地址: http://localhost:8000"
echo "API文档: http://localhost:8000/docs"
echo ""

cd /mnt/e/git_project/art_server
gnome-terminal -- bash -c "python main.py; exec bash" &

echo "等待后端启动..."
sleep 5

echo ""
echo "[2/2] 启动前端服务 (Vue3)..."
echo ""

cd /mnt/e/git_project/art-design-pro
gnome-terminal -- bash -c "pnpm dev; exec bash" &

echo ""
echo "========================================"
echo "  系统启动完成!"
echo "========================================"
echo ""
echo "后端地址: http://localhost:8000"
echo "API文档: http://localhost:8000/docs"
echo "前端地址: 请查看前端窗口输出"
echo ""
