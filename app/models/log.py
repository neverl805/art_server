"""API models for hCaptcha request monitoring."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RequestOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OTHER = "other"


class LogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    ip: str
    session_id: str
    timestamp: datetime
    request_id: str
    level: LogLevel
    module: str
    function: str
    line: int
    event: str
    message: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_line: str


class TraceSpan(BaseModel):
    attempt: int
    category: str
    sequence: int
    name: str
    start_ms: float | None = None
    duration_ms: float
    method: str | None = None
    host: str | None = None
    path: str | None = None
    status: int | None = None
    response_bytes: int | None = None
    ok: bool | None = None
    engine_ms: float | None = None
    peak_memory_bytes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TraceMetrics(BaseModel):
    attempts: int = 0
    queue_wait_ms: float = 0
    total_ms: float = 0
    http_total_ms: float = 0
    sandbox_total_ms: float = 0
    sandbox_engine_total_ms: float = 0
    sandbox_peak_memory_bytes: int = 0


class FingerprintSnapshot(BaseModel):
    fingerprint_key: str | None = None
    profile_variant: str | None = None
    profile_id: str | None = None
    locale: str | None = None
    timezone: str | None = None
    hcaptcha_version: str | None = None
    vmdata_length: int | None = None
    vmdata_slots: int | None = None
    n_length: int | None = None
    request_type: str | None = None
    task_count: int | None = None
    proxy_scheme: str | None = None
    proxy_host: str | None = None
    proxy_port: int | None = None
    proxy_endpoint: str | None = None
    proxy_endpoint_key: str | None = None
    proxy_session_mode: str | None = None
    proxy_country: str | None = None
    proxy_city: str | None = None
    proxy_timezone: str | None = None
    proxy_geo_source: str | None = None
    proxy_exit_ip: str | None = None
    proxy_asn: str | None = None
    proxy_isp: str | None = None
    locale_geo_match: bool | None = None
    timezone_geo_match: bool | None = None


class LogGroup(BaseModel):
    request_id: str
    session_id: str
    count: int
    start_time: datetime
    end_time: datetime
    duration_ms: float
    levels: dict[str, int]
    ip: str
    has_error: bool
    outcome: RequestOutcome
    method: str | None = None
    path: str | None = None
    http_status: int | None = None
    target_host: str | None = None
    attempts: int | None = None
    upstream_requests: int | None = None
    direct: bool | None = None
    token_hint: str | None = None
    token_remaining: int | None = None
    token_used: int | None = None
    error: str | None = None
    trace_metrics: TraceMetrics = Field(default_factory=TraceMetrics)
    fingerprint: FingerprintSnapshot = Field(default_factory=FingerprintSnapshot)
    spans: list[TraceSpan] = Field(default_factory=list)
    logs: list[LogEntry] = Field(default_factory=list)


class LogSearchParams(BaseModel):
    request_id: str | None = None
    outcome: RequestOutcome | None = None
    level: LogLevel | None = None
    ip: str | None = None
    module: str | None = None
    target_host: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    keyword: str | None = None
    include_non_solve: bool = False
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class TimelinePoint(BaseModel):
    time: str
    total: int
    success: int = 0
    failure: int = 0
    rejected: int = 0


class TargetStat(BaseModel):
    host: str
    total: int
    success: int
    failure: int
    success_rate: float
    average_duration_ms: float


class ClientStat(BaseModel):
    ip: str
    count: int


class TokenState(BaseModel):
    token_hint: str
    remaining: int
    used: int
    pending: int
    enabled: bool
    expires_at: float | None = None


class TokenUsage(BaseModel):
    available: bool
    remaining: int = 0
    used: int = 0
    pending: int = 0
    tokens: list[TokenState] = Field(default_factory=list)


class SourceStatus(BaseModel):
    log_dir: str
    database_path: str
    source_files: int
    indexed_logs: int
    parse_failures: int
    latest_log_at: datetime | None = None
    last_sync_at: datetime | None = None


class ServiceStatus(BaseModel):
    online: bool
    url: str
    checked_at: datetime
    engine_available: bool | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class LogOverviewStats(BaseModel):
    window_hours: int
    solve_total: int
    success_count: int
    failure_count: int
    rejected_count: int
    in_progress_count: int
    success_rate: float
    average_duration_ms: float
    p95_duration_ms: float
    direct_rate: float
    upstream_request_count: int
    log_total: int
    level_distribution: dict[str, int]
    timeline_data: list[TimelinePoint]
    target_stats: list[TargetStat]
    client_stats: list[ClientStat]
    recent_requests: list[LogGroup]
    token_usage: TokenUsage
    source: SourceStatus
    service: ServiceStatus


class LogListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    data: list[LogGroup]


class FingerprintCluster(BaseModel):
    key: str
    label: str
    dimensions: dict[str, Any]
    total: int
    terminal: int
    success: int
    failure: int
    success_rate: float
    direct_rate: float
    average_duration_ms: float
    p95_duration_ms: float
    average_http_ms: float
    average_sandbox_ms: float


class FingerprintClusterResponse(BaseModel):
    window_hours: int
    group_by: list[str]
    sample_total: int
    covered_samples: int
    coverage_rate: float
    clusters: list[FingerprintCluster]


class SyncResponse(BaseModel):
    imported: int
    parsed: int
    parse_failures: int
    source_files: int
    synced_at: datetime
