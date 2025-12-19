"""日志相关API"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from pydantic import BaseModel
from app.models.log import (
    LogSearchParams, LogOverviewStats,
    LogListResponse, LogGroup, LogLevel
)
from app.services.log_service_redis import log_service_redis as log_service
from app.database.redis_logger import redis_logger_manager

router = APIRouter(prefix="/api/logs", tags=["日志管理"])


class CleanLogsRequest(BaseModel):
    """清除日志请求参数"""
    days: int


class CleanLogsResponse(BaseModel):
    """清除日志响应"""
    deleted_count: int
    message: str


@router.get("/overview", response_model=LogOverviewStats, summary="获取日志总览统计")
async def get_overview():
    """
    获取日志总览统计信息

    返回:
    - total: 总日志数
    - error_count: 错误数
    - warning_count: 警告数
    - info_count: INFO数
    - debug_count: DEBUG数
    - request_count: 请求数
    - ip_count: IP数
    - level_distribution: 级别分布
    - timeline_data: 时间线数据
    - ip_stats: IP统计
    - recent_logs: 最近日志
    """
    try:
        stats = log_service.get_overview_stats()
        return stats
    except Exception as e:
        import traceback
        error_detail = f"获取统计数据失败: {str(e)}\n{traceback.format_exc()}"
        print(f"[ERROR] {error_detail}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list", response_model=LogListResponse, summary="获取日志列表")
async def get_log_list(
    request_id: Optional[str] = Query(None, description="请求ID"),
    level: Optional[LogLevel] = Query(None, description="日志级别"),
    ip: Optional[str] = Query(None, description="IP地址"),
    module: Optional[str] = Query(None, description="模块名"),
    start_time: Optional[str] = Query(None, description="开始时间(YYYY-MM-DD HH:MM:SS)"),
    end_time: Optional[str] = Query(None, description="结束时间(YYYY-MM-DD HH:MM:SS)"),
    keyword: Optional[str] = Query(None, description="关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数")
):
    """
    获取日志列表（按request_id分组）

    支持多维度搜索:
    - request_id: 请求ID
    - level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
    - ip: IP地址
    - module: 模块名
    - start_time: 开始时间
    - end_time: 结束时间
    - keyword: 关键词搜索
    - page: 页码
    - page_size: 每页条数

    返回:
    - total: 总数
    - page: 当前页
    - page_size: 每页条数
    - data: 日志分组列表
    """
    try:
        # 转换时间字符串
        start_dt = None
        end_dt = None

        if start_time:
            try:
                start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise HTTPException(status_code=400, detail="开始时间格式错误，请使用 YYYY-MM-DD HH:MM:SS")

        if end_time:
            try:
                end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                raise HTTPException(status_code=400, detail="结束时间格式错误，请使用 YYYY-MM-DD HH:MM:SS")

        # 构建搜索参数
        params = LogSearchParams(
            request_id=request_id,
            level=level,
            ip=ip,
            module=module,
            start_time=start_dt,
            end_time=end_dt,
            keyword=keyword,
            page=page,
            page_size=page_size
        )

        return log_service.search_logs(params)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = f"获取日志列表失败: {str(e)}\n{traceback.format_exc()}"
        print(f"[ERROR] {error_detail}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail/{request_id}", response_model=LogGroup, summary="获取日志详情")
async def get_log_detail(request_id: str):
    """
    获取指定request_id的日志详情

    参数:
    - request_id: 请求ID

    返回:
    - request_id: 请求ID
    - count: 日志条数
    - start_time: 开始时间
    - end_time: 结束时间
    - duration_ms: 持续时间(毫秒)
    - levels: 各级别日志数量
    - ip: IP地址
    - has_error: 是否包含错误
    - logs: 日志列表
    """
    try:
        log_group = log_service.get_log_detail(request_id)
        if not log_group:
            raise HTTPException(status_code=404, detail=f"未找到 request_id 为 {request_id} 的日志")
        return log_group
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取日志详情失败: {str(e)}")


@router.delete("/clean", response_model=CleanLogsResponse, summary="清除旧日志")
async def clean_old_logs(days: int = Query(..., ge=0, description="保留最近N天的日志，删除更早的日志；设置为0则清除所有日志")):
    """
    清除旧日志

    参数:
    - days: 保留最近N天的日志（>= 0）
      - 设置为 0：清除所有日志
      - 设置为 N (N > 0)：保留最近N天，删除更早的日志

    返回:
    - deleted_count: 删除的日志数量
    - message: 操作结果消息

    示例:
    - days=0: 清除所有日志
    - days=7: 保留最近7天的日志，删除7天前的所有日志
    - days=30: 保留最近30天的日志，删除30天前的所有日志
    """
    try:
        if not redis_logger_manager.initialized:
            raise HTTPException(status_code=500, detail="Redis未初始化")

        # 清除所有日志
        if days == 0:
            # Redis中没有直接统计所有日志的方法，使用flushdb清空当前数据库
            try:
                # 获取当前日志数量（近似值）
                total_count = redis_logger_manager.redis_client.zcard('logs:timeline')

                if total_count == 0:
                    return CleanLogsResponse(
                        deleted_count=0,
                        message="没有日志需要清除"
                    )

                # 清空当前数据库（谨慎使用！）
                redis_logger_manager.redis_client.flushdb()

                return CleanLogsResponse(
                    deleted_count=total_count,
                    message=f"成功清除了所有日志，共约{total_count}条"
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"清除所有日志失败: {str(e)}")

        # 清除N天前的日志
        redis_logger_manager.clean_old_logs(days)

        return CleanLogsResponse(
            deleted_count=0,  # Redis的clean_old_logs会在内部打印删除数量
            message=f"成功清除了{days}天前的日志"
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = f"清除日志失败: {str(e)}\n{traceback.format_exc()}"
        print(f"[ERROR] {error_detail}")
        raise HTTPException(status_code=500, detail=str(e))


