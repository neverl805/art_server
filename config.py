"""Environment-driven configuration for the hCaptcha monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_HCAPTCHA_ROOT = PROJECT_ROOT.parent / "js_reverse" / "hcaptcha"


def _path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser().resolve()


def _origins() -> tuple[str, ...]:
    raw = os.getenv(
        "MONITOR_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("MONITOR_HOST", "127.0.0.1")
    port: int = int(os.getenv("MONITOR_PORT", "8000"))
    hcaptcha_root: Path = _path("HCAPTCHA_ROOT", DEFAULT_HCAPTCHA_ROOT)
    monitor_database: Path = _path(
        "MONITOR_DATABASE", PROJECT_ROOT / "data" / "monitor.db"
    )
    sync_interval_seconds: float = float(
        os.getenv("MONITOR_SYNC_INTERVAL_SECONDS", "2")
    )
    retention_days: int = int(os.getenv("MONITOR_RETENTION_DAYS", "2"))
    stale_request_seconds: int = int(
        os.getenv("MONITOR_STALE_REQUEST_SECONDS", "240")
    )
    service_url: str = os.getenv(
        "HCAPTCHA_SERVICE_URL", "http://127.0.0.1:43333"
    ).rstrip("/")
    service_probe_timeout_seconds: float = float(
        os.getenv("HCAPTCHA_PROBE_TIMEOUT_SECONDS", "1")
    )
    cors_origins: tuple[str, ...] = _origins()

    @property
    def log_dir(self) -> Path:
        return self.hcaptcha_root / "logs"

    @property
    def service_database(self) -> Path:
        return self.hcaptcha_root / "data" / "service.db"
