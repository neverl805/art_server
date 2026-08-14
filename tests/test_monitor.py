from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import json
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

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


def payload_message(event: str, payload: dict[str, object]) -> str:
    return f"{event} payload=" + json.dumps(payload, separators=(",", ":"))



class FakeNode:
    """An in-process stand-in for one hCaptcha service's `/admin/events`.

    The monitor no longer reads files, so the fixture cannot be a directory any more: it has
    to be something that answers the endpoint. Keeping it in-process (rather than mocking the
    ingestor) means the tests still exercise the real HTTP path, the real cursor handshake and
    the real admin-secret check, which is where multi-node ingestion can actually go wrong.

    The cursor is `lines:<index>`, not the service's `<file>:<offset>`. The ingestor treats it
    as opaque and must never parse it -- using a deliberately different shape here is what
    proves that.
    """

    def __init__(self, name: str, secret: str = "fixture-secret") -> None:
        self.name = name
        self.secret = secret
        self.lines: list[str] = []
        self.requests = 0
        self._lock = threading.Lock()
        node = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # keep the test output clean
                pass

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(self.path)
                if parsed.path != "/admin/events":
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.headers.get("X-Admin-Secret") != node.secret:
                    body = json.dumps({"code": 403, "message": "admin secret is invalid"}).encode()
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                query = parse_qs(parsed.query)
                since = query.get("since", [""])[0]
                limit = int(query.get("limit", ["2000"])[0])
                start = int(since.split(":", 1)[1]) if since.startswith("lines:") else 0
                with node._lock:
                    node.requests += 1
                    chunk = node.lines[start:start + limit]
                    total = len(node.lines)
                body = json.dumps({
                    "code": 200,
                    "node": node.name,
                    "lines": chunk,
                    "next_cursor": f"lines:{min(start + len(chunk), total)}",
                    "eof": not chunk,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def emit(self, *lines: str) -> None:
        with self._lock:
            self.lines.extend(line.rstrip("\n") for line in lines)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Node:
    """Mirrors `config.Node` without importing it, so a config change cannot silently
    invalidate what these tests think they are passing in."""

    def __init__(self, name: str, url: str, secret: str) -> None:
        self.name = name
        self.url = url
        self.secret = secret


class MonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "hcaptcha"
        self.log_dir = self.root / "logs"
        self.data_dir = self.root / "data"
        self.log_dir.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.monitor_database = Path(self.temp.name) / "monitor.db"
        self.node = FakeNode("node-a")
        self._write_sources()
        self._write_token_database()
        self.service = MonitorService(
            nodes=(Node("node-a", self.node.url, self.node.secret),),
            monitor_database=self.monitor_database,
            service_database=self.data_dir / "service.db",
            service_url="http://127.0.0.1:9",
            probe_timeout_seconds=0.05,
            retention_days=2,
        )

    def tearDown(self) -> None:
        self.node.close()
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
                started + timedelta(milliseconds=1),
                "INFO",
                "success-request",
                payload_message(
                    "request",
                    {
                        "method": "POST",
                        "path": "/get_hcaptcha_key",
                        "headers": {"x-client": "fixture"},
                        "body": {
                            "token": "full-api-token",
                            "proxies": "http://user:password@gateway.fixture:8000",
                            "sitekey": "fixture-sitekey",
                            "url": "https://example.com/form",
                            "page_html": "<html>fixture</html>",
                        },
                    },
                ),
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
                started + timedelta(seconds=2, milliseconds=15),
                "INFO",
                "success-request",
                payload_message(
                    "response",
                    {
                        "status": 200,
                        "body": {
                            "code": 200,
                            "Captcha_n": "full-captcha-token",
                        },
                    },
                ),
            ),
            log_line(
                started + timedelta(seconds=2, milliseconds=20),
                "INFO",
                "success-request",
                "request completed method=POST path=/get_hcaptcha_key "
                "status=200 elapsed_ms=2020.000",
            ),
        ]
        self.node.emit(*success_lines)

        failed = started + timedelta(minutes=1)
        failure_lines = [
            log_line(
                failed,
                "INFO",
                "failure-request",
                "request started method=POST path=/v1/hcaptcha/solve",
            ),
            log_line(
                failed + timedelta(milliseconds=1),
                "INFO",
                "failure-request",
                payload_message(
                    "request",
                    {
                        "method": "POST",
                        "path": "/v1/hcaptcha/solve",
                        "headers": {"x-client": "fixture"},
                        "body": {
                            "token": "full-api-token",
                            "proxies": "http://user:password@gateway.fixture:8000",
                            "sitekey": "fixture-sitekey",
                            "url": "https://example.net/form",
                        },
                    },
                ),
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
                failed + timedelta(seconds=3, milliseconds=15),
                "INFO",
                "failure-request",
                payload_message(
                    "response",
                    {
                        "status": 502,
                        "body": {"code": 400, "message": "fixture timeout"},
                    },
                ),
            ),
            log_line(
                failed + timedelta(seconds=3, milliseconds=20),
                "INFO",
                "failure-request",
                "request completed method=POST path=/v1/hcaptcha/solve "
                "status=502 elapsed_ms=3020.000",
            ),
        ]
        self.node.emit(*failure_lines)

    def _write_token_database(self) -> None:
        with closing(sqlite3.connect(self.data_dir / "service.db")) as connection:
            connection.executescript(
                """
                CREATE TABLE api_tokens (
                    token_hash TEXT PRIMARY KEY, token_value TEXT, token_hint TEXT,
                    remaining INTEGER, used INTEGER, enabled INTEGER,
                    expires_at REAL, created_at REAL
                );
                CREATE TABLE token_reservations (
                    reservation_id TEXT PRIMARY KEY, token_hash TEXT, status TEXT
                );
                INSERT INTO api_tokens VALUES (
                    'hash', 'full-token', '***oken', 10, 2, 1, NULL, 1
                );
                INSERT INTO api_tokens VALUES (
                    'disabled', 'disabled-token', '***abled', 72, 8, 0, NULL, 2
                );
                INSERT INTO token_reservations VALUES ('reservation', 'hash', 'pending');
                """
            )
            connection.commit()

    def test_incremental_sync_resumes_from_the_node_cursor(self) -> None:
        first = self.service.sync()
        second = self.service.sync()

        self.assertEqual(first.imported, 18)
        self.assertEqual(second.imported, 0)
        overview = self.service.get_overview(24)
        self.assertEqual(overview.solve_total, 2)
        self.assertEqual(overview.success_count, 1)
        self.assertEqual(overview.failure_count, 1)
        self.assertEqual(overview.success_rate, 50)
        self.assertEqual(overview.token_usage.remaining, 10)
        self.assertEqual(overview.token_usage.pending, 1)
        self.assertEqual(overview.token_usage.tokens[0].token, "full-token")

        # New events on the node are picked up, and only once.
        self.node.emit(
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

        # The cursor is persisted per node under an opaque key, and is whatever the NODE said
        # it was -- the monitor must never construct or interpret it.
        state = self.service.repository.source_state("node://node-a")
        self.assertIsNotNone(state)
        self.assertEqual(state["cursor"], f"lines:{len(self.node.lines)}")

        # A sync with nothing new must still not re-fetch from zero: the request count grows
        # by a bounded amount, not by the whole backlog.
        before = self.node.requests
        self.service.sync()
        self.assertLessEqual(self.node.requests - before, 2)

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
        self.assertEqual(len(detail.logs), 9)
        self.assertEqual(len(detail.spans), 4)
        self.assertEqual(detail.spans[1].name, "getcaptcha")
        self.assertEqual(detail.trace_metrics.http_total_ms, 1200)
        self.assertEqual(detail.trace_metrics.queue_wait_ms, 45.25)
        self.assertEqual(detail.fingerprint.proxy_country, "DE")
        request_log = next(
            entry for entry in detail.logs if entry.event == "request_payload"
        )
        response_log = next(
            entry for entry in detail.logs if entry.event == "response_payload"
        )
        self.assertEqual(
            request_log.attributes["payload"]["body"]["proxies"],
            "http://user:password@gateway.fixture:8000",
        )
        self.assertEqual(
            request_log.attributes["payload"]["body"]["token"],
            "full-api-token",
        )
        self.assertEqual(response_log.attributes["payload"]["status"], 502)

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
        # Drain the setUp fixtures first, so `imported` below counts only the old line.
        self.service.sync()

        old = datetime.now() - timedelta(days=3)
        self.node.emit(
            log_line(
                old,
                "INFO",
                "old-request",
                "request started method=POST path=/get_hcaptcha_key",
            )
        )

        before = self.service.repository.storage_bytes()
        result = self.service.sync()

        # Out-of-retention rows are now rejected at insert time rather than inserted and then
        # swept. The observable guarantee is unchanged -- they are not queryable -- but they
        # also never occupy a page, which is what stopped a first two-node sync from inflating
        # the index to 290 MB of mostly free space.
        self.assertIsNone(self.service.get_request_detail("old-request"))
        self.assertEqual(result.imported, 0, "an out-of-retention line must not be indexed at all")
        self.assertLessEqual(
            self.service.repository.storage_bytes() - before,
            65536,
            "indexing nothing should not grow the database",
        )

    def test_stale_in_progress_request_is_marked_interrupted(self) -> None:
        stale = datetime.now() - timedelta(minutes=10)
        self.node.emit(
            log_line(
                stale,
                "INFO",
                "stale-request",
                "request started method=POST path=/get_hcaptcha_key",
            ),
            log_line(
                stale + timedelta(seconds=1),
                "INFO",
                "stale-request",
                "token reserved hint=***oken remaining=8 pending=1",
            ),
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

        # After a manual clear the node keeps serving from where the cursor left off, so a
        # replay is a line that arrives AFTER the cutoff but is stamped BEFORE it. It must be
        # rejected on its timestamp, while a genuinely new line is still accepted.
        old = datetime.now() - timedelta(minutes=10)
        self.node.emit(
            log_line(
                old,
                "INFO",
                "replayed-request",
                "request started method=POST path=/get_hcaptcha_key",
            ),
            log_line(
                datetime.now() + timedelta(milliseconds=10),
                "INFO",
                "new-request",
                "request started method=POST path=/get_hcaptcha_key",
            ),
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
            # Without this the app falls back to the default local node and the whole API
            # surface reports zeroes -- which is exactly what a misconfigured MONITOR_NODES
            # looks like in production, so it is worth the test pinning it explicitly.
            nodes=(Node("node-a", self.node.url, self.node.secret),),
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

    def test_token_admin_api_proxies_live_ledger_operations(self) -> None:
        settings = Settings(
            hcaptcha_root=self.root,
            monitor_database=self.monitor_database,
            sync_interval_seconds=60,
            service_url="http://127.0.0.1:9",
            service_admin_secret="admin-secret",
            service_probe_timeout_seconds=0.05,
        )
        app = create_app(settings)
        record = {
            "token_id": "a" * 64,
            "token": "full-token",
            "token_hint": "***oken",
            "remaining": 20,
            "used": 3,
            "pending": 0,
            "enabled": True,
            "expires_at": None,
            "created_at": 1,
            "updated_at": 2,
        }
        app.state.monitor.list_token_records = Mock(
            return_value={
                "total": 1,
                "remaining": 20,
                "used": 3,
                "pending": 0,
                "tokens": [record],
            }
        )
        app.state.monitor.create_token_record = Mock(return_value=record)
        app.state.monitor.update_token_record = Mock(return_value=record)
        app.state.monitor.delete_token_record = Mock(return_value=record)

        with TestClient(app) as client:
            listing = client.get("/api/logs/tokens")
            created = client.post(
                "/api/logs/tokens",
                json={
                    "token": "raw-token",
                    "remaining": 20,
                    "enabled": True,
                    "expires_at": None,
                },
            )
            updated = client.patch(
                f"/api/logs/tokens/{record['token_id']}",
                json={"remaining": 20, "enabled": False},
            )
            deleted = client.delete(f"/api/logs/tokens/{record['token_id']}")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["data"]["total"], 1)
        self.assertEqual(listing.json()["data"]["tokens"][0]["token"], "full-token")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        app.state.monitor.create_token_record.assert_called_once_with(
            {
                "token": "raw-token",
                "remaining": 20,
                "enabled": True,
                "expires_at": None,
            }
        )
        app.state.monitor.update_token_record.assert_called_once_with(
            record["token_id"], {"remaining": 20, "enabled": False}
        )


if __name__ == "__main__":
    unittest.main()


class MultiNodeTest(unittest.TestCase):
    """The behaviours that only exist once there is more than one node.

    The single-node suite above cannot see any of these: host attribution is trivially correct
    with one host, a dedup collision needs two producers, and "one node down" is not a state a
    one-node deployment has.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "monitor.db"
        self.node_a = FakeNode("node-a")
        self.node_b = FakeNode("node-b")
        self.service = MonitorService(
            nodes=(
                Node("node-a", self.node_a.url, self.node_a.secret),
                Node("node-b", self.node_b.url, self.node_b.secret),
            ),
            monitor_database=self.database,
            service_database=Path(self.temp.name) / "service.db",
            service_url="http://127.0.0.1:9",
            probe_timeout_seconds=0.05,
        )

    def tearDown(self) -> None:
        self.node_a.close()
        self.node_b.close()
        self.temp.cleanup()

    def _started(self, request_id: str, when: datetime) -> str:
        return log_line(when, "INFO", request_id, "request started method=POST path=/get_hcaptcha_key")

    def test_rows_are_attributed_to_the_node_they_came_from(self) -> None:
        now = datetime.now()
        self.node_a.emit(self._started("req-a", now))
        self.node_b.emit(self._started("req-b", now))

        self.service.sync()

        with self.service.repository.connection() as connection:
            hosts = {
                row["request_id"]: row["host"]
                for row in connection.execute("SELECT request_id, host FROM request_summaries")
            }
        self.assertEqual(hosts, {"req-a": "node-a", "req-b": "node-b"})

    def test_an_identical_line_from_two_nodes_is_two_events(self) -> None:
        """The dedup fingerprint must include the host.

        `log_entries.fingerprint` is UNIQUE and was hashed from the raw line alone. Most lines
        carry a uuid request id and could never collide, but SYSTEM-scoped ones carry no
        per-request entropy at all -- so with two nodes, the same startup line would silently
        drop one node's copy and the panel would under-report that node.
        """
        identical = log_line(
            datetime.now(), "INFO", "SYSTEM", "request started method=GET path=/health"
        )
        self.node_a.emit(identical)
        self.node_b.emit(identical)

        self.service.sync()

        with self.service.repository.connection() as connection:
            rows = connection.execute(
                "SELECT host FROM log_entries WHERE message LIKE '%path=/health%'"
            ).fetchall()
        self.assertEqual(sorted(row["host"] for row in rows), ["node-a", "node-b"])

    def test_one_unreachable_node_does_not_stop_the_others(self) -> None:
        now = datetime.now()
        self.node_a.emit(self._started("req-a", now))
        self.node_b.emit(self._started("req-b", now))
        self.node_b.close()  # node-b is now refusing connections

        result = self.service.sync()

        self.assertEqual(result.source_files, 1, "only the reachable node should be counted")
        self.assertIsNotNone(self.service.get_request_detail("req-a"))
        # node-b's cursor must be untouched, so it resumes rather than restarts when it returns.
        self.assertIsNone(self.service.repository.source_state("node://node-b"))

    def test_a_wrong_admin_secret_fails_that_node_only(self) -> None:
        now = datetime.now()
        self.node_a.emit(self._started("req-a", now))
        self.node_b.emit(self._started("req-b", now))
        self.node_b.secret = "rotated-out-of-band"

        result = self.service.sync()

        self.assertEqual(result.source_files, 1)
        self.assertIsNotNone(self.service.get_request_detail("req-a"))
        self.assertIsNone(self.service.get_request_detail("req-b"))


class TimestampPrecisionTest(unittest.TestCase):
    """The service writes nanoseconds; `fromisoformat` before 3.11 accepts only 3 or 6 digits.

    Moving the panel from a Python 3.14 host to a 3.10 one made every line fail to parse, and
    the only symptom was `parse_failures` rising beside `imported: 0` -- which reads like
    corrupt input rather than an interpreter difference. Pinned here so the panel stays
    runnable on whatever Python a host happens to have.
    """

    def test_a_nanosecond_timestamp_parses_on_every_interpreter(self):
        from app.utils.log_parser import LogParser

        for stamp in (
            "2026-08-12T09:05:35.752677786+08:00",  # nanoseconds, what the service writes
            "2026-08-12T09:05:35.752677+08:00",     # microseconds
            "2026-08-12T09:05:35.752+08:00",        # milliseconds
            "2026-08-12T09:05:35+08:00",            # none at all
        ):
            line = json.dumps({"ts": stamp, "level": "INFO", "event": "request_started", "data": {}})
            entry = LogParser.parse_line(line)
            self.assertIsNotNone(entry, f"{stamp} failed to parse")
            self.assertEqual(entry.timestamp.year, 2026)

    def test_a_timestamp_that_is_not_a_timestamp_is_still_rejected(self):
        from app.utils.log_parser import LogParser

        line = json.dumps({"ts": "not-a-time", "level": "INFO", "event": "e", "data": {}})
        self.assertIsNone(LogParser.parse_line(line))
