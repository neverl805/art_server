"""FastAPI日志中间件"""
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.logger.config import log_context


class LoggingMiddleware(BaseHTTPMiddleware):
    """日志中间件 - 自动注入请求上下文"""

    async def dispatch(self, request: Request, call_next):
        """
        处理请求，自动添加日志上下文

        Args:
            request: 请求对象
            call_next: 下一个处理器

        Returns:
            响应对象
        """
        # 生成请求ID
        request_id = str(uuid.uuid4())[:8]

        # 获取客户端IP
        client_ip = request.client.host if request.client else "unknown"

        # 获取logger并绑定上下文
        logger = log_context.get_logger(ip=client_ip, request_id=request_id)

        # 记录请求开始
        logger.info(f"请求开始: {request.method} {request.url.path}")

        try:
            # 处理请求
            response = await call_next(request)

            # 记录请求完成
            logger.info(
                f"请求完成: {request.method} {request.url.path} - 状态码: {response.status_code}"
            )

            return response

        except Exception as e:
            # 记录异常
            logger.error(
                f"请求异常: {request.method} {request.url.path} - 错误: {str(e)}"
            )
            raise
