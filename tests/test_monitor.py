from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.log import LogSearchParams, RequestOutcome
from app.services.monitor_service import MonitorService
from config import Settings
from main import create_app


def log_line(
    timestamp: datetime,
    level: str,
    request_id: str,
    message: str,
    *,
    function: str = "solve_request",
    line: int = 100,
) -> str:
    return (
        f"{timestamp:%Y-%m-%d %H:%M:%S.%f}"[:-3]
        + f" | {level:<8} | ip=127.0.0.1 session=session-{request_id} "
        + f"request={request_id} | server:{function}:{line} | {message}\n"
    )


def trace_message(*, country: str, profile: str, success: bool) -> str:
    payload = {
        "schema_version": 1,
        "attempt": 1,
        "success": success,
        "timing": {
            "total_ms": 1800,
            "http_total_ms": 1200,
            "sandbox_total_ms": 420,
            "sandbox_engine_total_ms": 360,
            "sandbox_peak_memory_bytes": 134217728,
            "http": [
                {
                    "sequence": 1,
                    "method": "GET",
                    "host": "example.com",
                    "path": "/",
                    "status": 200,
                    "bytes": 512,
                    "start_ms": 0,
                    "duration_ms": 350,
                },
                {
                    "sequence": 2,
                    "method": "POST",
                    "host": "api.hcaptcha.com",
                    "path": "/getcaptcha/sitekey",
                    "status": 200,
                    "bytes": 1024,
                    "start_ms": 1200,
                    "duration_ms": 850,
                },
            ],
            "sandbox": [
                {
                    "sequence": 1,
                    "operation": "hsw",
                    "wall_ms": 420,
                    "engine_ms": 360,
                    "peak_memory_bytes": 134217728,
                    "ok": True,
                }
            ],
            "phases": [{"name": "prepare", "duration_ms": 900}],
        },
        "dimensions": {
            "profile_variant": profile,
            "profile_id": f"{profile}-{country.lower()}",
            "locale": "en-US",
            "timezone": "Europe/Berlin" if country == "DE" else "America/New_York",
            "hcaptcha_version": "v1-fixture",
            "vmdata_length": 2100,
            "vmdata_slots": 35,
            "n_length": 9200,
            "request_type": "direct",
            "task_count": 0,
            "proxy_scheme": "http",
            "proxy_host": "gateway.fixture",
            "proxy_port": 8000,
            "proxy_endpoint": "gateway.fixture:8000",
            "proxy_endpoint_key": "endpoint-key",
            "proxy_session_mode": "session",
            "proxy_country": country,
            "proxy_timezone": "Europe/Berlin" if country == "DE" else "America/New_York",
            "proxy_asn": "64500",
            "proxy_isp": "Fixture ISP",
            "locale_geo_match": country == "US",
            "timezone_geo_match": True,
        },
        "error": None if success else "fixture timeout",
    }
    return "hCaptcha trace payload=" + json.dumps(payload, separators=(",", ":"))


class MonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "hcaptcha"
        self.log_dir = self.root / "logs"
        self.data_dir = self.root / "data"
        self.log_dir.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.monitor_database = Path(self.temp.name) / "monitor.db"
        self._write_sources()
        self._write_token_database()
        self.service = MonitorService(
            log_dir=self.log_dir,
            monitor_database=self.monitor_database,
            service_database=self.data_dir / "service.db",
            service_url="http://127.0.0.1:9",
            probe_timeout_seconds=0.05,
            retention_days=2,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_sources(self) -> None:
        started = datetime.now() - timedelta(minutes=5)
        success_lines = [
            log_line(
                started,
                "INFO",
                "success-request",
                "request started method=POST path=/get_hcaptcha_key",
            ),
            log_line(
                started + timedelta(seconds=1),
                "INFO",
                "success-request",
                "token reserved hint=***oken remaining=9 pending=1",
            ),
            log_line(
                started + timedelta(seconds=1, milliseconds=5),
                "INFO",
                "success-request",
                "solver slot acquired queue_ms=12.500",
            ),
            log_line(
                started + timedelta(seconds=2),
                "INFO",
                "success-request",
                trace_message(country="US", profile="desktop", success=True),
            ),
            log_line(
                started + timedelta(seconds=2, milliseconds=5),
                "SUCCESS",
                "success-request",
                "hCaptcha solved host=example.com attempt=1 "
                "elapsed=2.000s requests=5 direct=True",
            ),
            log_line(
                started + timedelta(seconds=2, milliseconds=10),
                "INFO",
                "success-request",
                "token usage committed hint=***oken remaining=9 used=1",
            ),
            log_line(
                started + timedelta(seconds=2, milliseconds=20),
                "INFO",
                "success-request",
                "request completed method=POST path=/get_hcaptcha_key "
                "status=200 elapsed_ms=2020.000",
            ),
        ]
        archive = self.log_dir / "application_previous.log.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("application_previous.log", "".join(success_lines))

        failed = started + timedelta(minutes=1)
        failure_lines = [
            log_line(
                failed,
                "INFO",
                "failure-request",
                "request started method=POST path=/v1/hcaptcha/solve",
            ),
            log_line(
                failed + timedelta(seconds=1),
                "WARNING",
                "failure-request",
                "hCaptcha attempt failed host=example.net "
                "attempt=1/1 error=fixture timeout",
            ),
            log_line(
                failed + timedelta(seconds=1, milliseconds=5),
                "INFO",
                "failure-request",
                "solver slot acquired queue_ms=45.250",
            ),
            log_line(
                failed + timedelta(seconds=3),
                "INFO",
                "failure-request",
                trace_message(country="DE", profile="desktop", success=False),
            ),
            log_line(
                failed + timedelta(seconds=3, milliseconds=5),
                "ERROR",
                "failure-request",
                "hCaptcha failed host=example.net elapsed=3.000s attempts=1",
            ),
            log_line(
                failed + timedelta(seconds=3, milliseconds=10),
                "WARNING",
                "failure-request",
                "token usage refunded hint=***oken "
                "remaining=10 error=fixture timeout",
            ),
            log_line(
                failed + timedelta(seconds=3, milliseconds=20),
                "INFO",
                "failure-request",
                "request completed method=POST path=/v1/hcaptcha/solve "
                "status=502 elapsed_ms=3020.000",
            ),
        ]
        (self.log_dir / "application_current.log").write_text(
            "".join(failure_lines), encoding="utf-8"
        )

    def _write_token_database(self) -> None:
        with closing(sqlite3.connect(self.data_dir / "service.db")) as connection:
            connection.executescript(
                """
                CREATE TABLE api_tokens (
                    token_hash TEXT PRIMARY KEY, token_hint TEXT, remaining INTEGER,
                    used INTEGER, enabled INTEGER, expires_at REAL, created_at REAL
                );
                CREATE TABLE token_reservations (
                    reservation_id TEXT PRIMARY KEY, token_hash TEXT, status TEXT
                );
                INSERT INTO api_tokens VALUES ('hash', '***oken', 10, 2, 1, NULL, 1);
                INSERT INTO api_tokens VALUES ('disabled', '***abled', 72, 8, 0, NULL, 2);
                INSERT INTO token_reservations VALUES ('reservation', 'hash', 'pending');
                """
            )
            connection.commit()

    def test_incremental_sync_is_idempotent_and_indexes_archives(self) -> None:
        first = self.service.sync()
        second = self.service.sync()

        self.assertEqual(first.imported, 14)
        self.assertEqual(second.imported, 0)
        overview = self.service.get_overview(24)
        self.assertEqual(overview.solve_total, 2)
        self.assertEqual(overview.success_count, 1)
        self.assertEqual(overview.failure_count, 1)
        self.assertEqual(overview.success_rate, 50)
        self.assertEqual(overview.token_usage.remaining, 10)
        self.assertEqual(overview.token_usage.pending, 1)

        active_log = self.log_dir / "application_current.log"
        with active_log.open("a", encoding="utf-8") as handle:
            handle.write(
                log_line(
                    datetime.now(),
                    "INFO",
                    "health-request",
                    "request started method=GET path=/health",
                )
            )
        appended = self.service.sync()
        repeated = self.service.sync()
        self.assertEqual(appended.imported, 1)
        self.assertEqual(repeated.imported, 0)

        archive = self.log_dir / "application_previous.log.zip"
        archive_key = f"zip://{archive.resolve()}!application_previous.log"
        self.assertIsNotNone(self.service.repository.source_state(archive_key))
        archive.unlink()

        pruned = self.service.sync()

        self.assertEqual(pruned.pruned_sources, 1)
        self.assertIsNone(self.service.repository.source_state(archive_key))

    def test_search_and_detail_expose_hcaptcha_fields(self) -> None:
        self.service.sync()

        result = self.service.search_requests(
            LogSearchParams(outcome=RequestOutcome.SUCCESS)
        )
        detail = self.service.get_request_detail("failure-request")

        self.assertEqual(result.total, 1)
        self.assertEqual(result.data[0].target_host, "example.com")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.outcome, RequestOutcome.FAILURE)
        self.assertEqual(detail.error, "fixture timeout")
        self.assertEqual(len(detail.logs), 7)
        self.assertEqual(len(detail.spans), 4)
        self.assertEqual(detail.spans[1].name, "getcaptcha")
        self.assertEqual(detail.trace_metrics.http_total_ms, 1200)
        self.assertEqual(detail.trace_metrics.queue_wait_ms, 45.25)
        self.assertEqual(detail.fingerprint.proxy_country, "DE")

        clusters = self.service.get_fingerprint_clusters(
            hours=24,
            dimensions=["profile_variant", "proxy_country"],
        )
        self.assertEqual(clusters.covered_samples, 2)
        self.assertEqual(len(clusters.clusters), 2)
        self.assertEqual(
            {cluster.dimensions["proxy_country"] for cluster in clusters.clusters},
            {"US", "DE"},
        )

    def test_monitor_retention_removes_old_log_and_request_rows(self) -> None:
        old = datetime.now() - timedelta(days=3)
        (self.log_dir / "application_old.log").write_text(
            log_line(
                old,
                "INFO",
                "old-request",
                "request started method=POST path=/get_hcaptcha_key",
            ),
            encoding="utf-8",
        )

        result = self.service.sync()

        self.assertGreaterEqual(result.pruned, 2)
        self.assertIsNone(self.service.get_request_detail("old-request"))

    def test_stale_in_progress_request_is_marked_interrupted(self) -> None:
        stale = datetime.now() - timedelta(minutes=10)
        (self.log_dir / "application_stale.log").write_text(
            log_line(
                stale,
                "INFO",
                "stale-request",
                "request started method=POST path=/get_hcaptcha_key",
            )
            + log_line(
                stale + timedelta(seconds=1),
                "INFO",
                "stale-request",
                "token reserved hint=***oken remaining=8 pending=1",
            ),
            encoding="utf-8",
        )

        result = self.service.sync()
        detail = self.service.get_request_detail("stale-request")

        self.assertEqual(result.interrupted, 1)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.outcome, RequestOutcome.FAILURE)
        self.assertEqual(detail.error, "request interrupted before completion")

    def test_manual_cleanup_reclaims_index_and_only_accepts_new_logs(self) -> None:
        self.service.sync()

        result = self.service.clear_index()

        self.assertGreater(result.deleted_logs, 0)
        self.assertGreater(result.deleted_requests, 0)
        self.assertGreater(result.source_records_preserved, 0)
        self.assertGreater(result.database_bytes_after, 0)
        self.assertEqual(
            result.reclaimed_bytes,
            max(0, result.database_bytes_before - result.database_bytes_after),
        )
        self.assertIsNone(self.service.get_request_detail("success-request"))

        old = datetime.now() - timedelta(minutes=10)
        with zipfile.ZipFile(
            self.log_dir / "application_replayed.log.zip", "w"
        ) as bundle:
            bundle.writestr(
                "application_replayed.log",
                log_line(
                    old,
                    "INFO",
                    "replayed-request",
                    "request started method=POST path=/get_hcaptcha_key",
                ),
            )
        with (self.log_dir / "application_current.log").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(
                log_line(
                    datetime.now() + timedelta(milliseconds=10),
                    "INFO",
                    "new-request",
                    "request started method=POST path=/get_hcaptcha_key",
                )
            )

        self.service.sync()

        self.assertIsNone(self.service.get_request_detail("replayed-request"))
        self.assertIsNotNone(self.service.get_request_detail("new-request"))

    def test_api_contract_wraps_monitor_data(self) -> None:
        settings = Settings(
            hcaptcha_root=self.root,
            monitor_database=self.monitor_database,
            sync_interval_seconds=60,
            service_url="http://127.0.0.1:9",
            service_probe_timeout_seconds=0.05,
        )
        with TestClient(create_app(settings)) as client:
            overview = client.get("/api/logs/overview?hours=24")
            listing = client.get("/api/logs/list?outcome=failure")
            detail = client.get("/api/logs/detail/failure-request")
            clusters = client.get(
                "/api/logs/fingerprint-clusters"
                "?hours=24&dimensions=profile_variant,proxy_country"
            )
            rejected_cleanup = client.post(
                "/api/logs/cleanup", json={"confirm": False}
            )
            cleanup = client.post("/api/logs/cleanup", json={"confirm": True})
            empty_overview = client.get("/api/logs/overview?hours=24")

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["data"]["solve_total"], 2)
        self.assertEqual(listing.json()["data"]["total"], 1)
        self.assertEqual(detail.json()["data"]["target_host"], "example.net")
        self.assertEqual(clusters.status_code, 200)
        self.assertEqual(clusters.json()["data"]["covered_samples"], 2)
        self.assertEqual(rejected_cleanup.status_code, 422)
        self.assertEqual(cleanup.status_code, 200)
        self.assertGreater(cleanup.json()["data"]["deleted_total"], 0)
        self.assertEqual(empty_overview.json()["data"]["solve_total"], 0)


if __name__ == "__main__":
    unittest.main()
