@echo off
chcp 65001 >nul
echo ========================================
echo   日志可视化分析系统 - 启动脚本
echo ========================================
echo.

echo [1/2] 启动后端服务 (FastAPI)...
echo 后端地址: http://localhost:8000
echo API文档: http://localhost:8000/docs
echo.

cd /d E:\git_project\art_server
start "日志分析系统-后端" cmd /k "python main.py"

echo 等待后端启动...
timeout /t 5 /nobreak >nul

echo.
echo [2/2] 启动前端服务 (Vue3)...
echo.

cd /d E:\git_project\art-design-pro
start "日志分析系统-前端" cmd /k "pnpm dev"

echo.
echo ========================================
echo   系统启动完成!
echo ========================================
echo.
echo 后端地址: http://localhost:8000
echo API文档: http://localhost:8000/docs
echo 前端地址: 请查看前端窗口输出
echo.
echo 按任意键退出...
pause >nul
