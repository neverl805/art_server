"""Operational commands for the rebuildable monitor index."""

from __future__ import annotations

import argparse
import json

from app.services.monitor_service import MonitorService
from config import Settings


def create_service() -> MonitorService:
    settings = Settings()
    return MonitorService(
        log_dir=settings.log_dir,
        monitor_database=settings.monitor_database,
        service_database=settings.service_database,
        service_url=settings.service_url,
        probe_timeout_seconds=settings.service_probe_timeout_seconds,
        retention_days=settings.retention_days,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the hCaptcha monitor index")
    parser.add_argument("command", choices=("sync", "stats"))
    args = parser.parse_args()
    service = create_service()
    if args.command == "sync":
        result = service.sync()
        print(json.dumps(result.__dict__, default=str, indent=2))
        return
    overview = service.get_overview(24)
    print(
        json.dumps(
            {
                "solves_24h": overview.solve_total,
                "success_rate": overview.success_rate,
                "average_duration_ms": overview.average_duration_ms,
                "indexed_logs": overview.source.indexed_logs,
                "tokens_remaining": overview.token_usage.remaining,
                "hcaptcha_online": overview.service.online,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
