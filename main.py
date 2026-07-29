"""FastAPI entrypoint for the local Safari hCaptcha monitor."""

from __future__ import annotations

import asyncio
import os
import resource
from contextlib import asynccontextmanager, suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.api import logs_router
from app.logger import setup_logger
from app.services.monitor_service import MonitorService
from config import Settings


_PAGE_SIZE = resource.getpagesize()


def _resident_bytes() -> int:
    """Resident set size, or 0 where the platform does not expose it."""

    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            return int(handle.read().split()[1]) * _PAGE_SIZE
    except (OSError, IndexError, ValueError):
        return 0


async def _sync_loop(
    monitor: MonitorService,
    stop: asyncio.Event,
    interval_seconds: float,
    memory_limit_bytes: int,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(monitor.sync)
        except Exception:
            logger.exception("background log sync failed")
        resident = _resident_bytes()
        if memory_limit_bytes and resident > memory_limit_bytes:
            # Exiting non-zero hands the restart to supervisor. Waiting for the
            # kernel instead fails the entire supervisor unit.
            logger.critical(
                "resident memory {} MB exceeded the {} MB limit, exiting for restart",
                resident // (1024 * 1024),
                memory_limit_bytes // (1024 * 1024),
            )
            await logger.complete()
            os._exit(1)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    setup_logger()
    monitor = MonitorService(
        log_dir=config.log_dir,
        monitor_database=config.monitor_database,
        service_database=config.service_database,
        service_url=config.service_url,
        service_admin_secret=config.service_admin_secret,
        probe_timeout_seconds=config.service_probe_timeout_seconds,
        retention_days=config.retention_days,
        stale_request_seconds=config.stale_request_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(monitor.sync)
        stop = asyncio.Event()
        task = asyncio.create_task(
            _sync_loop(
                monitor,
                stop,
                config.sync_interval_seconds,
                config.memory_limit_mb * 1024 * 1024,
            )
        )
        logger.info(
            "monitor started log_dir={} database={}",
            config.log_dir,
            config.monitor_database,
        )
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.info("monitor stopped")
            await logger.complete()

    app = FastAPI(
        title="Safari hCaptcha Monitor",
        description="Local request, latency, outcome, and token usage monitoring",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.monitor = monitor
    app.state.settings = config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.status_code, "msg": str(error.detail), "data": None},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, error: Exception) -> JSONResponse:
        logger.exception("monitor request failed: {}", error)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": "internal server error", "data": None},
        )

    app.include_router(logs_router)

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "code": 200,
            "msg": "success",
            "data": {"name": "Safari hCaptcha Monitor", "docs": "/docs"},
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        source = monitor.get_source_status()
        service = monitor.probe_service()
        return {
            "code": 200,
            "msg": "success",
            "data": {
                "status": "ok",
                "hcaptcha_online": service.online,
                "indexed_logs": source.indexed_logs,
                "last_sync_at": (
                    source.last_sync_at.isoformat() if source.last_sync_at else None
                ),
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    settings = app.state.settings
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        access_log=False,
    )
