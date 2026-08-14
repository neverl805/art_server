# hCaptcha Monitoring Architecture

## Decision

Each hCaptcha service keeps writing its own JSONL event files and exposes them read-only over
an authenticated `GET /admin/events`. The monitor on 154 **pulls** from every configured node
into one local SQLite index, which is the single queryable store for the whole fleet. The Vue
client polls the FastAPI monitor every 10 seconds. No queue, no message broker, and no
database daemon sits between the services and the monitor.

This replaces the previous design, in which the monitor globbed a local log directory. That
worked while the monitor and the service shared a host and stopped working the moment a second
service appeared on another box: a local directory and an inode-plus-byte-offset cursor have
no meaning across a network, so 189's traffic was invisible to the panel no matter how healthy
189 itself was.

## Why pull, not push

The alternative was a shipper inside each service pushing batches to an ingest endpoint. Pull
was chosen because the failure modes are strictly better for a captcha service:

- The endpoint is read-only and touches nothing a solve depends on. The worst it can do to a
  node is nothing at all. A shipper living inside the service shares its process and its fate,
  and logging must never be able to slow or block a solve.
- The monitor sets the rate, so a backlog drains at a speed we choose instead of arriving as a
  flood after an outage.
- Each node's log files are already the durable buffer. If the monitor is down, nothing is
  lost and nothing new has to be persisted anywhere; when it returns, the cursor resumes.
- No inbound port and no new daemon on either box.

The cost is that the monitor must be able to reach each node, and that events appear in the
panel one poll interval late rather than immediately. Neither matters for an operations view.

## Data Flow

1. `RemoteLogIngestor` asks each node for `GET /admin/events?since=<cursor>&limit=<n>` with
   that node's admin secret.
2. The node returns verbatim JSONL lines, its own `node` name, and a `next_cursor`. It never
   parses or reshapes anything — parsing stays entirely on the monitor side, which already
   owns the parser, the schema and the dedup rule. A producer that knows the consumer's
   storage schema is the coupling this deployment already argued its way out of once.
3. The cursor is opaque to the monitor and is stored per node in `ingest_sources` under the
   key `node://<name>`. It is persisted after each batch, so a crash mid-catch-up costs one
   batch of re-reads rather than restarting the backlog.
4. `MonitorRepository` writes entries and request summaries to `data/monitor.db` in WAL mode,
   stamping every row with the `host` the monitor was configured to call that node.
5. SHA-256 fingerprints over `host + raw line` make retries idempotent. The host is part of
   the identity, not decoration: the same line from two nodes is two events, and hashing the
   line alone would silently drop one of them.
6. API queries join derived metrics with a read-only aggregate of the local service's token
   ledger and a bounded `/health` probe, and accept an optional `host` filter.
7. Token CRUD still proxies to the local hCaptcha `/admin/tokens`, so the service's
   transactional `TokenStore` remains the only ledger writer.

Failure is per node and never fatal. An unreachable node, or one that rejects the admin
secret, leaves its own cursor untouched and does not stop the others: a panel showing one
node's traffic beats a panel showing none.

## Identity

`host` is the name from `MONITOR_NODES`, not the name the node reports. The node's own
`HCAPTCHA_NODE_NAME` is returned in every batch and compared, and a mismatch is logged once
per sync — it usually means a configured url points at a different box than its name claims —
but the configured name wins, so a misconfigured service cannot choose which host's rows it is
written into. Node names must be stable: renaming one re-imports its history under a second
identity rather than correcting the old rows. Rows that predate multi-node ingestion carry the
placeholder `?`, which is deliberately not a legal node name.

## Configuration

    MONITOR_NODES='[{"name":"154","url":"http://127.0.0.1:43333","secret":"..."},
                    {"name":"189","url":"http://189.24.97.149:43333","secret":"..."}]'

A JSON array rather than a delimited string because the value carries per-node admin secrets
and the two deployments do not share one, so any single-character separator would be a
character a secret could not contain. Unset falls back to the single local service, which
keeps a one-box install working with no new configuration.

## Rationale

SQLite in WAL mode remains the store. WAL's constraint is that all *processes touching the
database file* are on one host — that is still true here, because the monitor is the only
writer and it runs on 154. The previous single-host constraint was about where the log
*sources* lived, and that is what the pull model removes. See <https://sqlite.org/wal.html>.

There is no volume argument for a server database at present: retention is short and the index
measures in low megabytes. Introducing PostgreSQL or MySQL would add a daemon to the more
resource-constrained of the two boxes to solve a problem the deployment does not yet have.

FastAPI lifespan owns initial synchronization and the background task so startup and shutdown
share one lifecycle: <https://fastapi.tiangolo.com/advanced/events/>.

## Scale-Out Boundary

Move to an OpenTelemetry Collector plus Loki or ClickHouse when retention grows beyond local
disk, when more than a handful of nodes must be polled, or when something other than this
panel needs to query the same events. At that point the collector should receive structured
events directly and the monitor API should query the centralized store. SQLite FTS5 can be
added earlier if substring searches become the local bottleneck: <https://sqlite.org/fts5.html>.
