# hCaptcha Monitoring Architecture

## Decision

Use the hCaptcha service's Loguru files as the immutable source, a local SQLite database as a rebuildable query index, and the existing token SQLite database as a read-only secondary source for overview queries. The Vue client polls the FastAPI monitor every 10 seconds. No queue or cache sits between the service and monitor.

This fits the current deployment: one hCaptcha process, one monitor process, one host, modest retention, and request-oriented queries. Redis previously duplicated durable data in memory and required producers to know the monitor's storage schema.

## Data Flow

1. `LogIngestor` reads active source files from their last byte offset and imports each archive member once. Lines are parsed and committed in bounded batches, and the stored offset advances with each batch, so peak memory tracks the batch size rather than the file size and a restart resumes mid-file. One pass reads at most 32 MB per file; a backlog drains across passes instead of holding the ingest lock.
2. SHA-256 line fingerprints make rotation and retries idempotent.
3. The parser extracts request/session/IP context and typed lifecycle events such as `solve_succeeded`, `solve_failed`, and `token_committed`.
4. `MonitorRepository` writes raw entries and request summaries to `data/monitor.db` in SQLite WAL mode.
5. API queries join derived metrics with a read-only aggregate of `hcaptcha/data/service.db` and a bounded `/health` probe.
6. Token CRUD requests pass through the monitor to the hCaptcha `/admin/tokens` API, so the service's transactional `TokenStore` remains the only ledger writer.

The monitor never opens the hCaptcha token ledger for writing or removes source logs. Its index can be deleted and rebuilt.

## Rationale

SQLite documents that WAL permits readers and a writer to proceed concurrently, which matches the background ingestor plus API query workload. WAL requires all processes to be on the same host, an intentional constraint for this deployment: <https://sqlite.org/wal.html>.

FastAPI lifespan owns initial synchronization and the background task so startup and shutdown share one lifecycle: <https://fastapi.tiangolo.com/advanced/events/>.

Loguru rotation, retention, compression, and queued sinks remain owned by the source service rather than being reimplemented: <https://loguru.readthedocs.io/en/stable/api/logger.html#loguru._logger.Logger.add>.

## Scale-Out Boundary

Move to an OpenTelemetry Collector plus Loki or ClickHouse when logs originate on multiple hosts, retention grows beyond local disk, or concurrent writers become necessary. At that point the collector should receive structured events directly and the monitor API should query the centralized store. SQLite FTS5 can be added earlier if substring searches become the local bottleneck: <https://sqlite.org/fts5.html>.
