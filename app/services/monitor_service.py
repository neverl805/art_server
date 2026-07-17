"""Query facade joining the log index, token ledger, and live service health."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from app.database.monitor import (
    CleanupResult,
    LogIngestor,
    MonitorRepository,
    SOLVE_PATHS,
    SyncResult,
)
from app.models.log import (
    ClientStat,
    FingerprintCluster,
    FingerprintClusterResponse,
    FingerprintSnapshot,
    LogEntry,
    LogGroup,
    LogLevel,
    LogListResponse,
    LogOverviewStats,
    LogSearchParams,
    RequestOutcome,
    ServiceStatus,
    SourceStatus,
    TargetStat,
    TimelinePoint,
    TokenState,
    TokenUsage,
    TraceMetrics,
    TraceSpan,
)


CLUSTER_DIMENSIONS = {
    "profile_variant": "Profile",
    "profile_id": "Profile ID",
    "locale": "语言",
    "timezone": "时区",
    "hcaptcha_version": "hCaptcha 版本",
    "vmdata_slots": "VMData slots",
    "proxy_scheme": "代理协议",
    "proxy_endpoint": "代理端点",
    "proxy_session_mode": "代理会话",
    "proxy_country": "代理国家",
    "proxy_asn": "代理 ASN",
    "proxy_isp": "代理 ISP",
    "proxy_timezone": "代理时区",
}


class MonitorService:
    def __init__(
        self,
        *,
        log_dir: Path,
        monitor_database: Path,
        service_database: Path,
        service_url: str,
        probe_timeout_seconds: float = 1,
        retention_days: int = 2,
        stale_request_seconds: int = 240,
    ) -> None:
        self.log_dir = log_dir
        self.service_database = service_database
        self.service_url = service_url
        self.probe_timeout_seconds = probe_timeout_seconds
        self.retention_days = max(1, retention_days)
        self.stale_request_seconds = max(30, stale_request_seconds)
        self.repository = MonitorRepository(monitor_database)
        self.ingestor = LogIngestor(log_dir, self.repository)
        self._sync_lock = threading.Lock()

    def sync(self) -> SyncResult:
        with self._sync_lock:
            result = self.ingestor.sync()
            interrupted = self.repository.mark_stale_solve_requests(
                time.time() - self.stale_request_seconds
            )
            pruned = self.repository.prune_before(
                time.time() - self.retention_days * 24 * 60 * 60
            )
            return SyncResult(
                imported=result.imported,
                parsed=result.parsed,
                parse_failures=result.parse_failures,
                source_files=result.source_files,
                synced_at=result.synced_at,
                pruned=pruned,
                interrupted=interrupted,
                pruned_sources=result.pruned_sources,
            )

    def clear_index(self) -> CleanupResult:
        with self._sync_lock:
            return self.repository.clear_index()

    def get_overview(self, hours: int = 24) -> LogOverviewStats:
        self.sync()
        cutoff = (datetime.now() - timedelta(hours=hours)).timestamp()
        with self.repository.connection() as connection:
            summary_rows = connection.execute(
                """
                SELECT * FROM request_summaries
                WHERE path IN (?, ?) AND started_epoch >= ?
                ORDER BY started_epoch DESC
                """,
                (*SOLVE_PATHS, cutoff),
            ).fetchall()
            level_rows = connection.execute(
                """
                SELECT level, COUNT(*) AS count FROM log_entries
                WHERE timestamp_epoch >= ? GROUP BY level
                """,
                (cutoff,),
            ).fetchall()
            log_total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM log_entries WHERE timestamp_epoch >= ?",
                    (cutoff,),
                ).fetchone()[0]
            )

        outcomes = Counter(str(row["outcome"]) for row in summary_rows)
        terminal_total = (
            outcomes[RequestOutcome.SUCCESS.value]
            + outcomes[RequestOutcome.FAILURE.value]
            + outcomes[RequestOutcome.REJECTED.value]
        )
        durations = sorted(
            float(row["duration_ms"])
            for row in summary_rows
            if row["duration_ms"] is not None
            and row["outcome"] in {"success", "failure"}
        )
        direct_values = [
            bool(row["direct"]) for row in summary_rows if row["direct"] is not None
        ]
        return LogOverviewStats(
            window_hours=hours,
            solve_total=len(summary_rows),
            success_count=outcomes[RequestOutcome.SUCCESS.value],
            failure_count=outcomes[RequestOutcome.FAILURE.value],
            rejected_count=outcomes[RequestOutcome.REJECTED.value],
            in_progress_count=outcomes[RequestOutcome.IN_PROGRESS.value],
            success_rate=self._percentage(
                outcomes[RequestOutcome.SUCCESS.value], terminal_total
            ),
            average_duration_ms=(
                round(sum(durations) / len(durations), 3) if durations else 0
            ),
            p95_duration_ms=self._percentile(durations, 0.95),
            direct_rate=self._percentage(sum(direct_values), len(direct_values)),
            upstream_request_count=sum(
                int(row["upstream_requests"] or 0) for row in summary_rows
            ),
            log_total=log_total,
            level_distribution={
                str(row["level"]): int(row["count"]) for row in level_rows
            },
            timeline_data=self._timeline(summary_rows),
            target_stats=self._target_stats(summary_rows),
            client_stats=self._client_stats(summary_rows),
            recent_requests=[
                self._group_from_summary(row, include_logs=False)
                for row in summary_rows[:10]
            ],
            token_usage=self.get_token_usage(),
            source=self.get_source_status(),
            service=self.probe_service(),
        )

    def search_requests(self, params: LogSearchParams) -> LogListResponse:
        self.sync()
        clauses: list[str] = []
        values: list[Any] = []
        if not params.include_non_solve:
            clauses.append("r.path IN (?, ?)")
            values.extend(SOLVE_PATHS)
        if params.request_id:
            clauses.append("r.request_id LIKE ?")
            values.append(f"%{params.request_id}%")
        if params.outcome:
            clauses.append("r.outcome = ?")
            values.append(params.outcome.value)
        if params.ip:
            clauses.append("r.ip LIKE ?")
            values.append(f"%{params.ip}%")
        if params.target_host:
            clauses.append("r.target_host LIKE ?")
            values.append(f"%{params.target_host}%")
        if params.start_time:
            clauses.append("r.started_epoch >= ?")
            values.append(params.start_time.timestamp())
        if params.end_time:
            clauses.append("r.started_epoch <= ?")
            values.append(params.end_time.timestamp())
        if params.level:
            clauses.append(
                "EXISTS (SELECT 1 FROM log_entries l "
                "WHERE l.request_id = r.request_id AND l.level = ?)"
            )
            values.append(params.level.value)
        if params.module:
            clauses.append(
                "EXISTS (SELECT 1 FROM log_entries l "
                "WHERE l.request_id = r.request_id AND l.module LIKE ?)"
            )
            values.append(f"%{params.module}%")
        if params.keyword:
            clauses.append(
                "(COALESCE(r.error, '') LIKE ? OR COALESCE(r.target_host, '') LIKE ? "
                "OR EXISTS (SELECT 1 FROM log_entries l "
                "WHERE l.request_id = r.request_id AND l.message LIKE ?))"
            )
            keyword = f"%{params.keyword}%"
            values.extend((keyword, keyword, keyword))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        offset = (params.page - 1) * params.page_size
        with self.repository.connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM request_summaries r{where}", values
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT r.* FROM request_summaries r{where}
                ORDER BY r.started_epoch DESC LIMIT ? OFFSET ?
                """,
                (*values, params.page_size, offset),
            ).fetchall()
        return LogListResponse(
            total=total,
            page=params.page,
            page_size=params.page_size,
            data=[self._group_from_summary(row, include_logs=True) for row in rows],
        )

    def get_request_detail(self, request_id: str) -> LogGroup | None:
        self.sync()
        with self.repository.connection() as connection:
            row = connection.execute(
                "SELECT * FROM request_summaries WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        return self._group_from_summary(row, include_logs=True, include_spans=True)

    def get_fingerprint_clusters(
        self,
        *,
        hours: int,
        dimensions: list[str],
        min_samples: int = 1,
    ) -> FingerprintClusterResponse:
        self.sync()
        group_by = list(dict.fromkeys(dimensions))
        invalid = [name for name in group_by if name not in CLUSTER_DIMENSIONS]
        if invalid:
            raise ValueError(f"unsupported cluster dimensions: {', '.join(invalid)}")
        if not group_by:
            group_by = ["profile_variant", "proxy_country", "hcaptcha_version"]
        cutoff = (datetime.now() - timedelta(hours=hours)).timestamp()
        with self.repository.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM request_summaries
                WHERE path IN (?, ?) AND started_epoch >= ?
                  AND outcome IN ('success', 'failure', 'rejected')
                ORDER BY started_epoch DESC
                """,
                (*SOLVE_PATHS, cutoff),
            ).fetchall()
        covered = [row for row in rows if row["fingerprint_key"]]
        grouped: dict[tuple[object, ...], list[sqlite3.Row]] = defaultdict(list)
        for row in covered:
            grouped[tuple(row[name] for name in group_by)].append(row)
        clusters: list[FingerprintCluster] = []
        for values, cluster_rows in grouped.items():
            if len(cluster_rows) < min_samples:
                continue
            dimensions_value = dict(zip(group_by, values, strict=True))
            success = sum(row["outcome"] == "success" for row in cluster_rows)
            failure = sum(
                row["outcome"] in {"failure", "rejected"} for row in cluster_rows
            )
            durations = sorted(
                float(row["duration_ms"])
                for row in cluster_rows
                if row["duration_ms"] is not None
            )
            direct_values = [
                bool(row["direct"])
                for row in cluster_rows
                if row["direct"] is not None
            ]
            http_values = [
                float(row["http_total_ms"])
                for row in cluster_rows
                if row["http_total_ms"] is not None
            ]
            sandbox_values = [
                float(row["sandbox_total_ms"])
                for row in cluster_rows
                if row["sandbox_total_ms"] is not None
            ]
            signature = json.dumps(
                dimensions_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            clusters.append(
                FingerprintCluster(
                    key=hashlib.sha256(signature.encode()).hexdigest()[:16],
                    label=" · ".join(
                        f"{CLUSTER_DIMENSIONS[name]}: {value or '--'}"
                        for name, value in dimensions_value.items()
                    ),
                    dimensions=dimensions_value,
                    total=len(cluster_rows),
                    terminal=success + failure,
                    success=success,
                    failure=failure,
                    success_rate=self._percentage(success, success + failure),
                    direct_rate=self._percentage(sum(direct_values), len(direct_values)),
                    average_duration_ms=(
                        round(sum(durations) / len(durations), 3) if durations else 0
                    ),
                    p95_duration_ms=self._percentile(durations, 0.95),
                    average_http_ms=(
                        round(sum(http_values) / len(http_values), 3)
                        if http_values
                        else 0
                    ),
                    average_sandbox_ms=(
                        round(sum(sandbox_values) / len(sandbox_values), 3)
                        if sandbox_values
                        else 0
                    ),
                )
            )
        clusters.sort(key=lambda item: (-item.total, -item.success_rate, item.label))
        return FingerprintClusterResponse(
            window_hours=hours,
            group_by=group_by,
            sample_total=len(rows),
            covered_samples=len(covered),
            coverage_rate=self._percentage(len(covered), len(rows)),
            clusters=clusters[:100],
        )

    def get_token_usage(self) -> TokenUsage:
        if not self.service_database.is_file():
            return TokenUsage(available=False)
        uri = f"file:{self.service_database}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    """
                    SELECT t.token_hint, t.remaining, t.used, t.enabled, t.expires_at,
                           COUNT(r.reservation_id) AS pending
                    FROM api_tokens t
                    LEFT JOIN token_reservations r
                      ON r.token_hash = t.token_hash AND r.status = 'pending'
                    GROUP BY t.token_hash ORDER BY t.created_at
                    """
                ).fetchall()
        except (sqlite3.Error, OSError):
            return TokenUsage(available=False)
        tokens = [
            TokenState(
                token_hint=str(row["token_hint"]),
                remaining=int(row["remaining"]),
                used=int(row["used"]),
                pending=int(row["pending"]),
                enabled=bool(row["enabled"]),
                expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            )
            for row in rows
        ]
        now = time.time()
        active_tokens = [
            token
            for token in tokens
            if token.enabled
            and (token.expires_at is None or token.expires_at > now)
        ]
        return TokenUsage(
            available=True,
            remaining=sum(token.remaining for token in active_tokens),
            used=sum(token.used for token in tokens),
            pending=sum(token.pending for token in tokens),
            tokens=tokens,
        )

    def get_source_status(self) -> SourceStatus:
        source_files = self.ingestor.source_files()
        source_bytes = 0
        for path in source_files:
            try:
                source_bytes += path.stat().st_size
            except FileNotFoundError:
                pass
        with self.repository.connection() as connection:
            source = connection.execute(
                """
                SELECT COALESCE(SUM(parse_failures), 0) AS failures
                FROM ingest_sources
                """
            ).fetchone()
            logs = connection.execute(
                "SELECT COUNT(*) AS count, MAX(timestamp) AS latest FROM log_entries"
            ).fetchone()
            state = connection.execute(
                "SELECT value FROM monitor_state WHERE key = 'last_sync_at'"
            ).fetchone()
        return SourceStatus(
            log_dir=str(self.log_dir),
            database_path=str(self.repository.path),
            source_files=len(source_files),
            indexed_logs=int(logs["count"]),
            parse_failures=int(source["failures"]),
            retention_days=self.retention_days,
            source_bytes=source_bytes,
            database_bytes=self.repository.storage_bytes(),
            latest_log_at=datetime.fromisoformat(logs["latest"]) if logs["latest"] else None,
            last_sync_at=datetime.fromisoformat(state["value"]) if state else None,
        )

    def probe_service(self) -> ServiceStatus:
        checked_at = datetime.now()
        url = f"{self.service_url}/health"
        try:
            with urllib.request.urlopen(url, timeout=self.probe_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return ServiceStatus(
                online=True,
                url=self.service_url,
                checked_at=checked_at,
                engine_available=payload.get("engine_available"),
                metrics=payload.get("metrics") or {},
            )
        except (OSError, ValueError, urllib.error.URLError) as error:
            return ServiceStatus(
                online=False,
                url=self.service_url,
                checked_at=checked_at,
                error=str(error),
            )

    def _group_from_summary(
        self,
        row: sqlite3.Row,
        *,
        include_logs: bool,
        include_spans: bool = False,
    ) -> LogGroup:
        logs = self._logs_for_request(str(row["request_id"])) if include_logs else []
        if logs:
            levels = dict(Counter(log.level.value for log in logs))
        else:
            levels = {}
        return LogGroup(
            request_id=str(row["request_id"]),
            session_id=str(row["session_id"]),
            count=int(row["log_count"]),
            start_time=datetime.fromisoformat(row["started_at"]),
            end_time=datetime.fromisoformat(row["ended_at"]),
            duration_ms=float(row["duration_ms"] or 0),
            levels=levels,
            ip=str(row["ip"]),
            has_error=bool(row["has_error"]) or row["outcome"] in {"failure", "rejected"},
            outcome=RequestOutcome(str(row["outcome"])),
            method=row["method"],
            path=row["path"],
            http_status=row["http_status"],
            target_host=row["target_host"],
            attempts=row["attempts"],
            upstream_requests=row["upstream_requests"],
            direct=bool(row["direct"]) if row["direct"] is not None else None,
            token_hint=row["token_hint"],
            token_remaining=row["token_remaining"],
            token_used=row["token_used"],
            error=row["error"],
            trace_metrics=TraceMetrics(
                attempts=int(row["trace_attempts"] or 0),
                queue_wait_ms=float(row["queue_wait_ms"] or 0),
                total_ms=float(row["trace_total_ms"] or 0),
                http_total_ms=float(row["http_total_ms"] or 0),
                sandbox_total_ms=float(row["sandbox_total_ms"] or 0),
                sandbox_engine_total_ms=float(row["sandbox_engine_total_ms"] or 0),
                sandbox_peak_memory_bytes=int(
                    row["sandbox_peak_memory_bytes"] or 0
                ),
            ),
            fingerprint=FingerprintSnapshot(
                fingerprint_key=row["fingerprint_key"],
                profile_variant=row["profile_variant"],
                profile_id=row["profile_id"],
                locale=row["locale"],
                timezone=row["timezone"],
                hcaptcha_version=row["hcaptcha_version"],
                vmdata_length=row["vmdata_length"],
                vmdata_slots=row["vmdata_slots"],
                n_length=row["n_length"],
                request_type=row["request_type"],
                task_count=row["task_count"],
                proxy_scheme=row["proxy_scheme"],
                proxy_host=row["proxy_host"],
                proxy_port=row["proxy_port"],
                proxy_endpoint=row["proxy_endpoint"],
                proxy_endpoint_key=row["proxy_endpoint_key"],
                proxy_session_mode=row["proxy_session_mode"],
                proxy_country=row["proxy_country"],
                proxy_city=row["proxy_city"],
                proxy_timezone=row["proxy_timezone"],
                proxy_geo_source=row["proxy_geo_source"],
                proxy_exit_ip=row["proxy_exit_ip"],
                proxy_asn=row["proxy_asn"],
                proxy_isp=row["proxy_isp"],
                locale_geo_match=(
                    bool(row["locale_geo_match"])
                    if row["locale_geo_match"] is not None
                    else None
                ),
                timezone_geo_match=(
                    bool(row["timezone_geo_match"])
                    if row["timezone_geo_match"] is not None
                    else None
                ),
            ),
            spans=(
                self._spans_for_request(str(row["request_id"]))
                if include_spans
                else []
            ),
            logs=logs,
        )

    def _spans_for_request(self, request_id: str) -> list[TraceSpan]:
        with self.repository.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM request_spans WHERE request_id = ?
                ORDER BY attempt, CASE category
                    WHEN 'http' THEN 1 WHEN 'sandbox' THEN 2 ELSE 3 END, sequence
                """,
                (request_id,),
            ).fetchall()
        return [
            TraceSpan(
                attempt=int(row["attempt"]),
                category=str(row["category"]),
                sequence=int(row["sequence"]),
                name=str(row["name"]),
                start_ms=(
                    float(row["start_ms"]) if row["start_ms"] is not None else None
                ),
                duration_ms=float(row["duration_ms"]),
                method=row["method"],
                host=row["host"],
                path=row["path"],
                status=row["status"],
                response_bytes=row["response_bytes"],
                ok=bool(row["ok"]) if row["ok"] is not None else None,
                engine_ms=(
                    float(row["engine_ms"])
                    if row["engine_ms"] is not None
                    else None
                ),
                peak_memory_bytes=row["peak_memory_bytes"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]

    def _logs_for_request(self, request_id: str) -> list[LogEntry]:
        with self.repository.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM log_entries WHERE request_id = ?
                ORDER BY timestamp_epoch, id
                """,
                (request_id,),
            ).fetchall()
        return [
            LogEntry(
                id=int(row["id"]),
                ip=str(row["ip"]),
                session_id=str(row["session_id"]),
                timestamp=datetime.fromisoformat(row["timestamp"]),
                request_id=str(row["request_id"]),
                level=LogLevel(str(row["level"])),
                module=str(row["module"]),
                function=str(row["function"]),
                line=int(row["line"]),
                event=str(row["event"]),
                message=str(row["message"]),
                attributes=json.loads(row["attributes_json"]),
                raw_line=str(row["raw_line"]),
            )
            for row in rows
        ]

    @staticmethod
    def _timeline(rows: Iterable[sqlite3.Row]) -> list[TimelinePoint]:
        buckets: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            bucket = datetime.fromisoformat(row["started_at"]).strftime("%Y-%m-%d %H:00:00")
            buckets[bucket][str(row["outcome"])] += 1
        return [
            TimelinePoint(
                time=bucket,
                total=sum(counts.values()),
                success=counts["success"],
                failure=counts["failure"],
                rejected=counts["rejected"],
            )
            for bucket, counts in sorted(buckets.items())
        ]

    @classmethod
    def _target_stats(cls, rows: Iterable[sqlite3.Row]) -> list[TargetStat]:
        targets: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            if row["target_host"]:
                targets[str(row["target_host"])].append(row)
        stats: list[TargetStat] = []
        for host, host_rows in targets.items():
            success = sum(row["outcome"] == "success" for row in host_rows)
            failure = sum(
                row["outcome"] in {"failure", "rejected"} for row in host_rows
            )
            durations = [
                float(row["duration_ms"])
                for row in host_rows
                if row["duration_ms"] is not None
            ]
            stats.append(
                TargetStat(
                    host=host,
                    total=len(host_rows),
                    success=success,
                    failure=failure,
                    success_rate=cls._percentage(success, success + failure),
                    average_duration_ms=(
                        round(sum(durations) / len(durations), 3)
                        if durations
                        else 0
                    ),
                )
            )
        return sorted(stats, key=lambda item: item.total, reverse=True)[:10]

    @staticmethod
    def _client_stats(rows: Iterable[sqlite3.Row]) -> list[ClientStat]:
        counts = Counter(str(row["ip"]) for row in rows)
        return [ClientStat(ip=ip, count=count) for ip, count in counts.most_common(10)]

    @staticmethod
    def _percentage(numerator: int, denominator: int) -> float:
        return round(numerator / denominator * 100, 2) if denominator else 0

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0
        index = max(0, math.ceil(len(values) * percentile) - 1)
        return round(values[index], 3)
