"""SQLite index and incremental ingestion for hCaptcha Loguru files."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from app.models.log import LogEntry
from app.utils.log_parser import LogParser


LOGGER = logging.getLogger(__name__)

SOLVE_PATHS = ("/get_hcaptcha_key", "/v1/hcaptcha/solve")

#: Stamped on rows that predate multi-node ingestion. Not a legal node name (a node name
#: comes from MONITOR_NODES and is a plain identifier), so "we did not know the host" is
#: always distinguishable from a host we did know.
LEGACY_HOST = "?"

TRACE_COLUMNS: dict[str, str] = {
    "queue_wait_ms": "REAL",
    "trace_attempts": "INTEGER",
    "trace_total_ms": "REAL",
    "http_total_ms": "REAL",
    "sandbox_total_ms": "REAL",
    "sandbox_engine_total_ms": "REAL",
    "sandbox_peak_memory_bytes": "INTEGER",
    "fingerprint_key": "TEXT",
    "profile_variant": "TEXT",
    "profile_id": "TEXT",
    "locale": "TEXT",
    "timezone": "TEXT",
    "hcaptcha_version": "TEXT",
    "vmdata_length": "INTEGER",
    "vmdata_slots": "INTEGER",
    "n_length": "INTEGER",
    "request_type": "TEXT",
    "task_count": "INTEGER",
    "proxy_scheme": "TEXT",
    "proxy_host": "TEXT",
    "proxy_port": "INTEGER",
    "proxy_endpoint": "TEXT",
    "proxy_endpoint_key": "TEXT",
    "proxy_session_mode": "TEXT",
    "proxy_country": "TEXT",
    "proxy_city": "TEXT",
    "proxy_timezone": "TEXT",
    "proxy_geo_source": "TEXT",
    "proxy_exit_ip": "TEXT",
    "proxy_asn": "TEXT",
    "proxy_isp": "TEXT",
    "locale_geo_match": "INTEGER",
    "timezone_geo_match": "INTEGER",
    "trace_json": "TEXT",
}


def _span_name(category: str, item: dict[str, object]) -> str:
    if category == "sandbox":
        return str(item.get("operation") or "sandbox")
    if category == "phase":
        return str(item.get("name") or "phase")
    path = str(item.get("path") or "/")
    host = str(item.get("host") or "")
    if "checksiteconfig" in path:
        return "checksiteconfig"
    if "getcaptcha" in path:
        return "getcaptcha"
    if path.endswith("/api.js"):
        return "api.js"
    if path.endswith("/hsw.js"):
        return "hsw.js"
    if "hcaptcha.html" in path:
        return "hcaptcha iframe"
    if path == "/" and host:
        return "target page"
    return path.rsplit("/", 1)[-1] or host or "HTTP request"


@dataclass(frozen=True)
class SyncResult:
    imported: int
    parsed: int
    parse_failures: int
    source_files: int
    synced_at: datetime
    pruned: int = 0
    interrupted: int = 0
    pruned_sources: int = 0


@dataclass(frozen=True)
class CleanupResult:
    deleted_logs: int
    deleted_requests: int
    deleted_spans: int
    source_records_preserved: int
    database_bytes_before: int
    database_bytes_after: int
    reclaimed_bytes: int
    cleaned_at: datetime


class MonitorRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS log_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    timestamp_epoch REAL NOT NULL,
                    level TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    function TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    message TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    raw_line TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_logs_timestamp
                    ON log_entries(timestamp_epoch DESC);
                CREATE INDEX IF NOT EXISTS idx_logs_request
                    ON log_entries(request_id, timestamp_epoch);
                CREATE INDEX IF NOT EXISTS idx_logs_level
                    ON log_entries(level, timestamp_epoch DESC);

                CREATE TABLE IF NOT EXISTS request_summaries (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    method TEXT,
                    path TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    started_epoch REAL NOT NULL,
                    ended_epoch REAL NOT NULL,
                    duration_ms REAL,
                    http_status INTEGER,
                    outcome TEXT NOT NULL DEFAULT 'other',
                    target_host TEXT,
                    attempts INTEGER,
                    upstream_requests INTEGER,
                    direct INTEGER,
                    token_hint TEXT,
                    token_remaining INTEGER,
                    token_used INTEGER,
                    error TEXT,
                    has_error INTEGER NOT NULL DEFAULT 0,
                    log_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_requests_started
                    ON request_summaries(started_epoch DESC);
                CREATE INDEX IF NOT EXISTS idx_requests_path_outcome
                    ON request_summaries(path, outcome, started_epoch DESC);
                CREATE INDEX IF NOT EXISTS idx_requests_host
                    ON request_summaries(target_host, started_epoch DESC);

                CREATE TABLE IF NOT EXISTS request_spans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    start_ms REAL,
                    duration_ms REAL NOT NULL,
                    method TEXT,
                    host TEXT,
                    path TEXT,
                    status INTEGER,
                    response_bytes INTEGER,
                    ok INTEGER,
                    engine_ms REAL,
                    peak_memory_bytes INTEGER,
                    details_json TEXT NOT NULL,
                    UNIQUE(request_id, attempt, category, sequence),
                    FOREIGN KEY(request_id) REFERENCES request_summaries(request_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_spans_request
                    ON request_spans(request_id, attempt, category, sequence);

                CREATE TABLE IF NOT EXISTS ingest_sources (
                    path TEXT PRIMARY KEY,
                    inode INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    offset INTEGER NOT NULL,
                    complete INTEGER NOT NULL,
                    parse_failures INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            existing_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(request_summaries)")
            }
            for name, sql_type in TRACE_COLUMNS.items():
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE request_summaries ADD COLUMN {name} {sql_type}"
                    )

            # `host` identifies which service produced a row. Added by migration rather than
            # in the CREATE TABLE above so an existing monitor.db keeps its history: rows that
            # predate multi-node ingestion get the placeholder below, which is deliberately
            # not a real node name so a query can tell "before we tracked this" apart from
            # "node whose name we know".
            for table in ("log_entries", "request_summaries"):
                columns = {
                    str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if "host" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN host TEXT NOT NULL DEFAULT '{LEGACY_HOST}'"
                    )
            # A remote node's position is an opaque cursor string, not a byte offset into
            # a local inode, so `ingest_sources` grows a column for it rather than overloading
            # the numeric `offset` the file ingestor used.
            source_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(ingest_sources)")
            }
            if "cursor" not in source_columns:
                connection.execute("ALTER TABLE ingest_sources ADD COLUMN cursor TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_host "
                "ON log_entries(host, timestamp_epoch DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_host_started "
                "ON request_summaries(host, started_epoch DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_fingerprint "
                "ON request_summaries(fingerprint_key, started_epoch DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_proxy_combo "
                "ON request_summaries(proxy_country, proxy_asn, proxy_endpoint_key, "
                "started_epoch DESC)"
            )

    def source_state(self, path: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM ingest_sources WHERE path = ?", (path,)
            ).fetchone()

    def set_node_cursor(self, path: str, cursor: str, parse_failures: int) -> None:
        """Persist one node's resume position.

        The file-shaped columns (`inode`, `size`, `mtime_ns`, `offset`) are meaningless for a
        remote source and are written as zeroes rather than being made nullable, so the table
        keeps one shape for both kinds of source and `prune_source_states` still works
        unchanged. `complete` stays 0: a live node is never finished.
        """
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO ingest_sources (
                    path, inode, size, mtime_ns, offset, complete,
                    parse_failures, updated_at, cursor
                ) VALUES (?, 0, 0, 0, 0, 0, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    parse_failures = excluded.parse_failures,
                    updated_at = excluded.updated_at,
                    cursor = excluded.cursor
                """,
                (path, parse_failures, time.time(), cursor),
            )

    def update_source(
        self,
        *,
        path: str,
        inode: int,
        size: int,
        mtime_ns: int,
        offset: int,
        complete: bool,
        parse_failures: int,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO ingest_sources (
                    path, inode, size, mtime_ns, offset, complete,
                    parse_failures, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    inode = excluded.inode,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    offset = excluded.offset,
                    complete = excluded.complete,
                    parse_failures = ingest_sources.parse_failures + excluded.parse_failures,
                    updated_at = excluded.updated_at
                """,
                (
                    path,
                    inode,
                    size,
                    mtime_ns,
                    offset,
                    int(complete),
                    parse_failures,
                    time.time(),
                ),
            )

    def prune_source_states(self, active_paths: Iterable[str]) -> int:
        """Remove ingestion offsets for source files that no longer exist."""

        active = set(active_paths)
        with self.connection() as connection:
            stale = [
                str(row["path"])
                for row in connection.execute("SELECT path FROM ingest_sources")
                if str(row["path"]) not in active
            ]
            connection.executemany(
                "DELETE FROM ingest_sources WHERE path = ?",
                ((path,) for path in stale),
            )
        return len(stale)

    def insert_entries(
        self, entries: Iterable[LogEntry], source: str, host: str, retention_epoch: float = 0.0
    ) -> int:
        """Index a batch, skipping anything already outside the retention window.

        `retention_epoch` matters much more now that sources are remote. A node keeps its own
        log files for its own retention, which is longer than the monitor's, so first contact
        with a node hands back its whole surviving history -- most of which the monitor's
        retention sweep would delete moments later. Inserting and then deleting those rows is
        not merely wasted work: it leaves the free pages behind, and on the first two-node
        sync it grew a 1.8 MB index into a 290 MB file holding 18 MB of live data, which was
        slow enough to make the panel unusable until a VACUUM. Filtering here keeps them out
        in the first place.
        """
        imported = 0
        with self.connection() as connection:
            cutoff_row = connection.execute(
                "SELECT value FROM monitor_state WHERE key = 'index_cutoff_epoch'"
            ).fetchone()
            cutoff_epoch = float(cutoff_row["value"]) if cutoff_row else 0.0
            cutoff_epoch = max(cutoff_epoch, retention_epoch)
            for entry in entries:
                if entry.timestamp.timestamp() <= cutoff_epoch:
                    continue
                # Host is part of the identity, not just an attribute: log_entries.fingerprint
                # is UNIQUE, so hashing the raw line alone would make a byte-identical line
                # from two nodes collide and silently drop one of them. Most lines carry a
                # uuid request id and could never collide, but SYSTEM-scoped ones (service
                # start, warnings) carry no per-request entropy at all.
                fingerprint = hashlib.sha256(
                    f"{host}\x00{entry.raw_line}".encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO log_entries (
                        fingerprint, timestamp, timestamp_epoch, level, ip,
                        session_id, request_id, module, function, line, event,
                        message, attributes_json, raw_line, source, host
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        entry.timestamp.isoformat(timespec="milliseconds"),
                        entry.timestamp.timestamp(),
                        entry.level.value,
                        entry.ip,
                        entry.session_id,
                        entry.request_id,
                        entry.module,
                        entry.function,
                        entry.line,
                        entry.event,
                        entry.message,
                        json.dumps(entry.attributes, ensure_ascii=False, separators=(",", ":")),
                        entry.raw_line,
                        source,
                        host,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                imported += 1
                if entry.request_id != "SYSTEM":
                    self._update_request(connection, entry, host)
        return imported

    def _update_request(self, connection: sqlite3.Connection, entry: LogEntry, host: str) -> None:
        timestamp = entry.timestamp.isoformat(timespec="milliseconds")
        epoch = entry.timestamp.timestamp()
        connection.execute(
            """
            INSERT INTO request_summaries (
                request_id, session_id, ip, started_at, ended_at,
                started_epoch, ended_epoch, has_error, log_count, host
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                session_id = excluded.session_id,
                ip = excluded.ip,
                host = excluded.host,
                started_at = CASE
                    WHEN excluded.started_epoch < request_summaries.started_epoch
                    THEN excluded.started_at ELSE request_summaries.started_at END,
                started_epoch = MIN(request_summaries.started_epoch, excluded.started_epoch),
                ended_at = CASE
                    WHEN excluded.ended_epoch > request_summaries.ended_epoch
                    THEN excluded.ended_at ELSE request_summaries.ended_at END,
                ended_epoch = MAX(request_summaries.ended_epoch, excluded.ended_epoch),
                has_error = MAX(request_summaries.has_error, excluded.has_error),
                log_count = request_summaries.log_count + 1
            """,
            (
                entry.request_id,
                entry.session_id,
                entry.ip,
                timestamp,
                timestamp,
                epoch,
                epoch,
                int(entry.level.value in {"ERROR", "CRITICAL"}),
                host,
            ),
        )
        attrs = entry.attributes
        if entry.event == "request_started":
            path = str(attrs.get("path", ""))
            connection.execute(
                """
                UPDATE request_summaries SET method = ?, path = ?,
                    outcome = CASE WHEN ? IN (?, ?) THEN 'in_progress' ELSE outcome END
                WHERE request_id = ?
                """,
                (
                    attrs.get("method"),
                    path,
                    path,
                    *SOLVE_PATHS,
                    entry.request_id,
                ),
            )
        elif entry.event == "request_completed":
            connection.execute(
                """
                UPDATE request_summaries SET method = ?, path = ?, http_status = ?,
                    duration_ms = COALESCE(duration_ms, ?), ended_at = ?, ended_epoch = ?,
                    outcome = CASE WHEN outcome IN ('other', 'in_progress')
                        THEN 'completed' ELSE outcome END
                WHERE request_id = ?
                """,
                (
                    attrs.get("method"),
                    attrs.get("path"),
                    attrs.get("status"),
                    attrs.get("elapsed_ms"),
                    timestamp,
                    epoch,
                    entry.request_id,
                ),
            )
        elif entry.event == "solver_queue":
            connection.execute(
                "UPDATE request_summaries SET queue_wait_ms = ? WHERE request_id = ?",
                (attrs.get("queue_ms"), entry.request_id),
            )
        elif entry.event == "solve_succeeded":
            connection.execute(
                """
                UPDATE request_summaries SET outcome = 'success', target_host = ?,
                    attempts = ?, duration_ms = ?,
                    upstream_requests = MAX(COALESCE(upstream_requests, 0), ?),
                    direct = ?
                WHERE request_id = ?
                """,
                (
                    attrs.get("host"),
                    attrs.get("attempt"),
                    float(attrs.get("elapsed", 0)) * 1000,
                    attrs.get("requests"),
                    int(bool(attrs.get("direct"))),
                    entry.request_id,
                ),
            )
        elif entry.event == "solve_attempt_failed":
            connection.execute(
                """
                UPDATE request_summaries SET target_host = ?, attempts = ?, error = ?
                WHERE request_id = ?
                """,
                (
                    attrs.get("host"),
                    attrs.get("attempts"),
                    attrs.get("error"),
                    entry.request_id,
                ),
            )
        elif entry.event == "solve_failed":
            connection.execute(
                """
                UPDATE request_summaries SET outcome = 'failure', target_host = ?,
                    attempts = ?, duration_ms = ?, has_error = 1
                WHERE request_id = ?
                """,
                (
                    attrs.get("host"),
                    attrs.get("attempts"),
                    float(attrs.get("elapsed", 0)) * 1000,
                    entry.request_id,
                ),
            )
        elif entry.event in {"token_reserved", "token_committed", "token_refunded"}:
            outcome_sql = "outcome"
            if entry.event == "token_refunded":
                outcome_sql = "CASE WHEN outcome = 'success' THEN outcome ELSE 'failure' END"
            connection.execute(
                f"""
                UPDATE request_summaries SET token_hint = ?, token_remaining = ?,
                    token_used = COALESCE(?, token_used), error = COALESCE(?, error),
                    outcome = {outcome_sql}
                WHERE request_id = ?
                """,
                (
                    attrs.get("token_hint"),
                    attrs.get("remaining"),
                    attrs.get("used"),
                    attrs.get("error"),
                    entry.request_id,
                ),
            )
        elif entry.event == "token_rejected":
            connection.execute(
                """
                UPDATE request_summaries SET outcome = 'rejected', error = ?
                WHERE request_id = ?
                """,
                (f"token rejected: {attrs.get('reason', 'unknown')}", entry.request_id),
            )
        elif entry.event in {"validation_failed", "unhandled_error"}:
            connection.execute(
                """
                UPDATE request_summaries SET outcome = 'failure', error = ?, has_error = 1
                WHERE request_id = ?
                """,
                (attrs.get("error"), entry.request_id),
            )
        elif entry.event == "hcaptcha_trace":
            payload = attrs.get("payload")
            if isinstance(payload, dict):
                self._update_trace(connection, entry.request_id, payload)

    def _update_trace(
        self,
        connection: sqlite3.Connection,
        request_id: str,
        payload: dict[str, object],
    ) -> None:
        timing = payload.get("timing")
        dimensions = payload.get("dimensions")
        if not isinstance(timing, dict):
            timing = {}
        if not isinstance(dimensions, dict):
            dimensions = {}
        attempt = max(1, int(payload.get("attempt") or 1))
        fingerprint_fields = (
            "profile_variant",
            "profile_id",
            "locale",
            "timezone",
            "hcaptcha_version",
            "vmdata_slots",
            "proxy_country",
            "proxy_asn",
            "proxy_isp",
            "proxy_endpoint_key",
        )
        fingerprint_payload = {
            key: dimensions.get(key) for key in fingerprint_fields
        }
        fingerprint_key = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        column_values = {
            key: dimensions.get(key)
            for key in TRACE_COLUMNS
            if key in dimensions
        }
        column_values.update(
            {
                "fingerprint_key": fingerprint_key,
                "trace_json": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
        bool_columns = {"locale_geo_match", "timezone_geo_match"}
        for key in bool_columns:
            if column_values.get(key) is not None:
                column_values[key] = int(bool(column_values[key]))
        assignments = ", ".join(f"{key} = ?" for key in column_values)
        values = list(column_values.values())
        http_items = timing.get("http")
        http_count = len(http_items) if isinstance(http_items, list) else 0
        connection.execute(
            f"""
            UPDATE request_summaries SET
                trace_attempts = MAX(COALESCE(trace_attempts, 0), ?),
                trace_total_ms = COALESCE(trace_total_ms, 0) + ?,
                http_total_ms = COALESCE(http_total_ms, 0) + ?,
                sandbox_total_ms = COALESCE(sandbox_total_ms, 0) + ?,
                sandbox_engine_total_ms = COALESCE(sandbox_engine_total_ms, 0) + ?,
                sandbox_peak_memory_bytes = MAX(
                    COALESCE(sandbox_peak_memory_bytes, 0), ?
                ),
                upstream_requests = COALESCE(upstream_requests, 0) + ?,
                {assignments}
            WHERE request_id = ?
            """,
            (
                attempt,
                float(timing.get("total_ms") or 0),
                float(timing.get("http_total_ms") or 0),
                float(timing.get("sandbox_total_ms") or 0),
                float(timing.get("sandbox_engine_total_ms") or 0),
                int(timing.get("sandbox_peak_memory_bytes") or 0),
                http_count,
                *values,
                request_id,
            ),
        )
        connection.execute(
            "DELETE FROM request_spans WHERE request_id = ? AND attempt = ?",
            (request_id, attempt),
        )
        for category, source_key in (
            ("http", "http"),
            ("sandbox", "sandbox"),
            ("phase", "phases"),
        ):
            items = timing.get(source_key)
            if not isinstance(items, list):
                continue
            for index, raw_item in enumerate(items, 1):
                if not isinstance(raw_item, dict):
                    continue
                sequence = int(raw_item.get("sequence") or index)
                duration = raw_item.get("duration_ms")
                if duration is None:
                    duration = raw_item.get("wall_ms") or 0
                connection.execute(
                    """
                    INSERT INTO request_spans (
                        request_id, attempt, category, sequence, name, start_ms,
                        duration_ms, method, host, path, status, response_bytes,
                        ok, engine_ms, peak_memory_bytes, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        attempt,
                        category,
                        sequence,
                        _span_name(category, raw_item),
                        raw_item.get("start_ms"),
                        float(duration),
                        raw_item.get("method"),
                        raw_item.get("host"),
                        raw_item.get("path"),
                        raw_item.get("status"),
                        raw_item.get("bytes"),
                        (
                            int(bool(raw_item.get("ok")))
                            if raw_item.get("ok") is not None
                            else None
                        ),
                        raw_item.get("engine_ms"),
                        raw_item.get("peak_memory_bytes"),
                        json.dumps(
                            raw_item, ensure_ascii=False, separators=(",", ":")
                        ),
                    ),
                )

    def set_last_sync(self, synced_at: datetime) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO monitor_state(key, value) VALUES ('last_sync_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (synced_at.isoformat(timespec="milliseconds"),),
            )

    def prune_before(self, cutoff_epoch: float) -> int:
        """Delete derived monitor data older than the retention cutoff."""

        with self.connection() as connection:
            logs = connection.execute(
                "DELETE FROM log_entries WHERE timestamp_epoch < ?",
                (cutoff_epoch,),
            ).rowcount
            requests = connection.execute(
                "DELETE FROM request_summaries WHERE ended_epoch < ?",
                (cutoff_epoch,),
            ).rowcount
        return int(logs or 0) + int(requests or 0)

    def storage_bytes(self) -> int:
        total = 0
        for path in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def clear_index(self) -> CleanupResult:
        """Clear derived rows while preserving source offsets and raw logs."""

        before = self.storage_bytes()
        cleaned_at = datetime.now()
        cutoff_epoch = cleaned_at.timestamp()
        with self.connection() as connection:
            deleted_logs = int(
                connection.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]
            )
            deleted_requests = int(
                connection.execute("SELECT COUNT(*) FROM request_summaries").fetchone()[0]
            )
            deleted_spans = int(
                connection.execute("SELECT COUNT(*) FROM request_spans").fetchone()[0]
            )
            source_records = int(
                connection.execute("SELECT COUNT(*) FROM ingest_sources").fetchone()[0]
            )
            connection.execute("DELETE FROM log_entries")
            connection.execute("DELETE FROM request_summaries")
            connection.execute(
                "DELETE FROM sqlite_sequence "
                "WHERE name IN ('log_entries', 'request_spans')"
            )
            connection.execute(
                """
                INSERT INTO monitor_state(key, value) VALUES ('index_cutoff_epoch', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(cutoff_epoch),),
            )

        with self.connect() as connection:
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = self.storage_bytes()
        return CleanupResult(
            deleted_logs=deleted_logs,
            deleted_requests=deleted_requests,
            deleted_spans=deleted_spans,
            source_records_preserved=source_records,
            database_bytes_before=before,
            database_bytes_after=after,
            reclaimed_bytes=max(0, before - after),
            cleaned_at=cleaned_at,
        )

    def mark_stale_solve_requests(self, cutoff_epoch: float) -> int:
        with self.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE request_summaries
                SET outcome = 'failure', has_error = 1,
                    duration_ms = NULL,
                    error = COALESCE(error, 'request interrupted before completion')
                WHERE path IN (?, ?) AND outcome = 'in_progress'
                  AND started_epoch < ?
                """,
                (*SOLVE_PATHS, cutoff_epoch),
            )
            connection.execute(
                """
                UPDATE request_summaries SET duration_ms = NULL
                WHERE error = 'request interrupted before completion'
                """
            )
        return int(cursor.rowcount or 0)



class RemoteLogIngestor:
    """Pulls events from every configured node's `/admin/events` and indexes them.

    Replaces the file-globbing ingestor this monitor used when it and the service shared a
    host. That design could only ever see one machine: it read a local directory, and its
    per-source cursor was an inode plus a byte offset, neither of which means anything across
    a network. `ARCHITECTURE.md` recorded the single-host assumption as deliberate, and this
    is the point where the deployment outgrew it -- there are now two services and one panel.

    Pull rather than push, on purpose. The endpoint is read-only and touches nothing a solve
    depends on, so the worst this can do to a captcha node is nothing at all; a shipper living
    inside the service would share its process and its fate. It also means the monitor sets
    the rate, so a backlog drains at a speed we choose instead of arriving as a flood.

    Failure is per node and never fatal: one unreachable node leaves its cursor untouched and
    the others still sync, because a monitor that shows one node's traffic beats a monitor
    that shows none.
    """

    #: `ingest_sources.path` is reused as an opaque key -- it already holds `zip://...!member`
    #: for archives, so a scheme prefix is the established idiom here, not a new convention.
    SOURCE_PREFIX = "node://"

    def __init__(self, nodes, repository: "MonitorRepository", *, batch_lines: int = 2000,
                 timeout_seconds: float = 10.0, max_batches: int = 20,
                 retention_days: int = 2) -> None:
        self.nodes = tuple(nodes)
        self.repository = repository
        self.parser = LogParser()
        self.batch_lines = batch_lines
        self.timeout_seconds = timeout_seconds
        self.max_batches = max_batches
        self.retention_days = max(1, retention_days)
        self._lock = threading.Lock()

    def _retention_epoch(self) -> float:
        return time.time() - self.retention_days * 86400

    def source_key(self, node_name: str) -> str:
        return f"{self.SOURCE_PREFIX}{node_name}"

    def sync(self) -> SyncResult:
        with self._lock:
            imported = parsed = failures = 0
            reachable = 0
            for node in self.nodes:
                try:
                    node_imported, node_parsed, node_failures = self._sync_node(node)
                except Exception as error:  # noqa: BLE001 - one node must not stop the rest
                    # Left deliberately broad: a node being down is an expected steady state
                    # here, not an exception worth propagating into the sync loop, and the
                    # ways it can fail span URLError, HTTPError, socket timeouts and malformed
                    # JSON from something that is not the service at all.
                    LOGGER.warning("node %s sync failed: %s: %s", node.name, type(error).__name__, error)
                    continue
                reachable += 1
                imported += node_imported
                parsed += node_parsed
                failures += node_failures
            synced_at = datetime.now()
            self.repository.set_last_sync(synced_at)
            return SyncResult(imported, parsed, failures, reachable, synced_at)

    def _sync_node(self, node) -> tuple[int, int, int]:
        key = self.source_key(node.name)
        state = self.repository.source_state(key)
        cursor = str(state["cursor"]) if state is not None and state["cursor"] else ""

        imported = parsed = failures = 0
        batches = 0
        warned_about_name = False
        while batches < self.max_batches:
            batches += 1
            query = {"limit": str(self.batch_lines)}
            if cursor:
                query["since"] = cursor
            url = f"{node.url}/admin/events?{urllib.parse.urlencode(query)}"
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"Accept": "application/json", "X-Admin-Secret": node.secret},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 403:
                    raise PermissionError(f"node {node.name} rejected the admin secret") from error
                raise

            # The node states its own identity; we file rows under the name WE were configured
            # with. Disagreement is worth surfacing -- it usually means a MONITOR_NODES url
            # points at a different box than its name claims -- but the configured name still
            # wins, so a service cannot choose which host's rows it is written into.
            claimed = str(payload.get("node") or "")
            if claimed and claimed != node.name and not warned_about_name:
                # Once per sync, not once per batch: a catch-up pass makes many requests and
                # the mismatch is a property of the node, not of the batch.
                warned_about_name = True
                LOGGER.warning(
                    "node %s reports its own name as %r; storing under the configured name",
                    node.name, claimed,
                )

            lines = payload.get("lines") or []
            entries: list[LogEntry] = []
            for raw in lines:
                entry = self.parser.parse_line(str(raw))
                if entry is None:
                    failures += 1
                else:
                    parsed += 1
                    entries.append(entry)
            imported += self.repository.insert_entries(
                entries, key, node.name, self._retention_epoch()
            )

            next_cursor = str(payload.get("next_cursor") or "")
            # Persist after each batch, not once at the end: a crash mid-catch-up then costs
            # one batch of re-reads (which the fingerprint dedup absorbs) instead of
            # restarting the whole backlog.
            if next_cursor and next_cursor != cursor:
                cursor = next_cursor
                self.repository.set_node_cursor(key, cursor, failures)
            if not lines or not next_cursor:
                break
        return imported, parsed, failures
