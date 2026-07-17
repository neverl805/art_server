"""hCaptcha monitoring API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.models.log import LogLevel, LogSearchParams, RequestOutcome
from app.services.monitor_service import MonitorService, TokenAdminError


router = APIRouter(prefix="/api/logs", tags=["hCaptcha monitor"])


class CleanupRequest(BaseModel):
    confirm: bool = False


class TokenCreateRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    remaining: int = Field(ge=0, le=1_000_000_000)
    enabled: bool = True
    expires_at: float | None = Field(default=None, ge=0)


class TokenUpdateRequest(BaseModel):
    remaining: int | None = Field(default=None, ge=0, le=1_000_000_000)
    used: int | None = Field(default=None, ge=0, le=1_000_000_000)
    enabled: bool | None = None
    expires_at: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_change(self) -> "TokenUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one token field is required")
        return self


def _service(request: Request) -> MonitorService:
    return request.app.state.monitor


def _success(data: Any) -> dict[str, Any]:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    return {"code": 200, "msg": "success", "data": data}


def _token_result(operation: Callable[[], Any]) -> dict[str, Any]:
    try:
        return _success(operation())
    except TokenAdminError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from error


@router.get("/overview", summary="Get hCaptcha service overview")
def get_overview(
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 30),
) -> dict[str, Any]:
    return _success(_service(request).get_overview(hours))


@router.get("/list", summary="Search hCaptcha requests")
def get_request_list(
    request: Request,
    request_id: str | None = None,
    outcome: RequestOutcome | None = None,
    level: LogLevel | None = None,
    ip: str | None = None,
    module: str | None = None,
    target_host: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    keyword: str | None = None,
    include_non_solve: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    params = LogSearchParams(
        request_id=request_id,
        outcome=outcome,
        level=level,
        ip=ip,
        module=module,
        target_host=target_host,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        include_non_solve=include_non_solve,
        page=page,
        page_size=page_size,
    )
    return _success(_service(request).search_requests(params))


@router.get(
    "/fingerprint-clusters",
    summary="Aggregate success rate by fingerprint and proxy dimensions",
)
def get_fingerprint_clusters(
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 30),
    dimensions: str = Query(
        "profile_variant,proxy_country,hcaptcha_version",
        min_length=1,
    ),
    min_samples: int = Query(1, ge=1, le=1000),
) -> dict[str, Any]:
    selected = [item.strip() for item in dimensions.split(",") if item.strip()]
    try:
        result = _service(request).get_fingerprint_clusters(
            hours=hours,
            dimensions=selected,
            min_samples=min_samples,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _success(result)


@router.get("/detail/{request_id}", summary="Get one hCaptcha request trace")
def get_request_detail(request: Request, request_id: str) -> dict[str, Any]:
    group = _service(request).get_request_detail(request_id)
    if group is None:
        raise HTTPException(status_code=404, detail="request not found")
    return _success(group)


@router.post("/sync", summary="Run an incremental log sync")
def sync_logs(request: Request) -> dict[str, Any]:
    result = _service(request).sync()
    return _success(
        {
            "imported": result.imported,
            "parsed": result.parsed,
            "parse_failures": result.parse_failures,
            "source_files": result.source_files,
            "pruned": result.pruned,
            "pruned_sources": result.pruned_sources,
            "interrupted": result.interrupted,
            "synced_at": result.synced_at.isoformat(timespec="milliseconds"),
        }
    )


@router.post("/cleanup", summary="Clear the derived log index and reclaim space")
def cleanup_logs(payload: CleanupRequest, request: Request) -> dict[str, Any]:
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="cleanup confirmation is required")
    result = _service(request).clear_index()
    return _success(
        {
            "deleted_logs": result.deleted_logs,
            "deleted_requests": result.deleted_requests,
            "deleted_spans": result.deleted_spans,
            "deleted_total": (
                result.deleted_logs + result.deleted_requests + result.deleted_spans
            ),
            "source_records_preserved": result.source_records_preserved,
            "database_bytes_before": result.database_bytes_before,
            "database_bytes_after": result.database_bytes_after,
            "reclaimed_bytes": result.reclaimed_bytes,
            "cleaned_at": result.cleaned_at.isoformat(timespec="milliseconds"),
        }
    )


@router.get("/tokens", summary="List live SQLite token records")
def list_tokens(request: Request) -> dict[str, Any]:
    return _token_result(_service(request).list_token_records)


@router.post("/tokens", summary="Create or reset a SQLite token record")
def create_token(payload: TokenCreateRequest, request: Request) -> dict[str, Any]:
    return _token_result(
        lambda: _service(request).create_token_record(
            payload.model_dump(mode="json")
        )
    )


@router.patch("/tokens/{token_id}", summary="Update a SQLite token record")
def update_token(
    token_id: str, payload: TokenUpdateRequest, request: Request
) -> dict[str, Any]:
    return _token_result(
        lambda: _service(request).update_token_record(
            token_id,
            payload.model_dump(
                mode="json",
                exclude_unset=True,
            ),
        )
    )


@router.delete("/tokens/{token_id}", summary="Delete a SQLite token record")
def delete_token(token_id: str, request: Request) -> dict[str, Any]:
    return _token_result(lambda: _service(request).delete_token_record(token_id))
