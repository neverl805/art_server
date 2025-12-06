"""FastAPI日志可视化分析系统"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api import logs_router
from app.logger import setup_logger, log_context
from app.middleware import LoggingMiddleware
from typing import Any

# 初始化日志系统
setup_logger()

# 创建FastAPI应用
app = FastAPI(
    title="日志可视化分析系统",
    description="基于FastAPI开发的日志可视化分析系统，支持日志解析、统计、搜索等功能",
    version="1.0.0"
)

# 配置CORS - 必须在其他中间件之前添加
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该指定具体的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]  # 允许前端访问所有响应头
)

# 添加日志中间件
app.add_middleware(LoggingMiddleware)


# 统一响应格式中间件
@app.middleware("http")
async def add_response_wrapper(request: Request, call_next):
    """统一包装响应格式"""
    response = await call_next(request)

    # 只处理API路由
    if request.url.path.startswith("/api"):
        # 获取原始响应
        import json
        from starlette.responses import Response

        if isinstance(response, Response):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            try:
                # 解析原始响应
                original_data = json.loads(body.decode())

                # 如果已经是标准格式，直接返回
                if isinstance(original_data, dict) and "code" in original_data:
                    # 保留CORS相关headers，过滤掉会导致冲突的headers
                    filtered_headers = {
                        k: v for k, v in response.headers.items()
                        if k.lower() not in ['content-length', 'content-encoding', 'transfer-encoding']
                    }
                    return JSONResponse(
                        content=original_data,
                        status_code=response.status_code,
                        headers=filtered_headers
                    )

                # 包装成标准格式
                wrapped_data = {
                    "code": response.status_code,
                    "msg": "success" if response.status_code == 200 else "error",
                    "data": original_data
                }

                # 保留CORS相关headers，过滤掉会导致冲突的headers
                filtered_headers = {
                    k: v for k, v in response.headers.items()
                    if k.lower() not in ['content-length', 'content-encoding', 'transfer-encoding']
                }
                return JSONResponse(
                    content=wrapped_data,
                    status_code=response.status_code,
                    headers=filtered_headers
                )
            except:
                # 如果解析失败，返回原始响应
                return Response(
                    content=body,
                    status_code=response.status_code,
                    media_type=response.media_type,
                    headers=dict(response.headers)
                )

    return response


# 注册路由
app.include_router(logs_router)


@app.get("/")
async def root():
    """根路径"""
    return {
        "code": 200,
        "msg": "success",
        "data": {
            "message": "日志可视化分析系统API",
            "version": "1.0.0",
            "docs": "/docs"
        }
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "code": 200,
        "msg": "success",
        "data": {"status": "ok"}
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
