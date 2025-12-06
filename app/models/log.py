"""日志数据模型"""
from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """日志级别枚举"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"  # loguru 内置级别
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogEntry(BaseModel):
    """单条日志实体"""
    id: Optional[int] = Field(None, description="日志ID")
    ip: str = Field(..., description="IP地址")
    timestamp: datetime = Field(..., description="时间戳")
    request_id: str = Field(..., description="请求ID")
    level: LogLevel = Field(..., description="日志级别")
    module: str = Field(..., description="模块名")
    function: str = Field(..., description="函数名")
    line: int = Field(..., description="行号")
    message: str = Field(..., description="日志消息")
    raw_line: str = Field(..., description="原始日志行")

    class Config:
        json_encoders = {
            datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        }


class LogGroup(BaseModel):
    """按request_id分组的日志"""
    request_id: str = Field(..., description="请求ID")
    count: int = Field(..., description="日志条数")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    duration_ms: float = Field(..., description="持续时间(毫秒)")
    levels: dict = Field(..., description="各级别日志数量")
    ip: str = Field(..., description="IP地址")
    has_error: bool = Field(False, description="是否包含错误")
    logs: List[LogEntry] = Field(default_factory=list, description="日志列表")

    class Config:
        json_encoders = {
            datetime: lambda v: v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        }


class LogSearchParams(BaseModel):
    """日志搜索参数"""
    request_id: Optional[str] = Field(None, description="请求ID")
    level: Optional[LogLevel] = Field(None, description="日志级别")
    ip: Optional[str] = Field(None, description="IP地址")
    module: Optional[str] = Field(None, description="模块名")
    start_time: Optional[datetime] = Field(None, description="开始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    keyword: Optional[str] = Field(None, description="关键词")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页条数")


class LogOverviewStats(BaseModel):
    """日志总览统计"""
    total: int = Field(..., description="总日志数")
    error_count: int = Field(..., description="错误数")
    warning_count: int = Field(..., description="警告数")
    info_count: int = Field(..., description="INFO数")
    success_count: int = Field(..., description="SUCCESS数")
    debug_count: int = Field(..., description="DEBUG数")
    request_count: int = Field(..., description="请求数(request_id数)")
    ip_count: int = Field(..., description="IP数")
    level_distribution: dict = Field(..., description="级别分布")
    timeline_data: List[dict] = Field(..., description="时间线数据")
    ip_stats: List[dict] = Field(..., description="IP统计")
    recent_logs: List[LogEntry] = Field(..., description="最近日志")


class LogListResponse(BaseModel):
    """日志列表响应"""
    total: int = Field(..., description="总数")
    page: int = Field(..., description="当前页")
    page_size: int = Field(..., description="每页条数")
    data: List[LogGroup] = Field(..., description="日志分组列表")
