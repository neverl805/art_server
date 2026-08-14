"""Environment-driven configuration for the hCaptcha monitor."""

from __future__ import annotations

import json
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
class Node:
    """One hCaptcha service the monitor pulls events from.

    `name` is the identity every row from this service is stored under, so it must be stable
    for the lifetime of the deployment: renaming a node re-imports its history as a second
    host rather than correcting the old rows. It should match that service's own
    `HCAPTCHA_NODE_NAME`, but the monitor's value is the one that wins -- the node's answer is
    recorded for cross-checking and logged when the two disagree, never silently trusted,
    since a misconfigured service could otherwise claim to be a node it is not and have its
    rows filed under that name.
    """

    name: str
    url: str
    secret: str


def _nodes() -> tuple[Node, ...]:
    """Nodes to pull from, as a JSON array in `MONITOR_NODES`.

        MONITOR_NODES='[{"name":"154","url":"http://127.0.0.1:43333","secret":"..."},
                        {"name":"189","url":"http://189.24.97.149:43333","secret":"..."}]'

    A JSON array rather than a delimited string on purpose: the value carries per-node admin
    secrets, and the two deployments do NOT share one, so any single-character separator is a
    secret that cannot contain that character. Unset falls back to the single local service
    the monitor already knew about, which keeps a one-box install working with no new config.
    """
    raw = os.getenv("MONITOR_NODES", "").strip()
    if not raw:
        return (
            Node(
                name=os.getenv("MONITOR_LOCAL_NODE_NAME", "local"),
                url=os.getenv("HCAPTCHA_SERVICE_URL", "http://127.0.0.1:43333").rstrip("/"),
                secret=os.getenv("HCAPTCHA_ADMIN_SECRET", ""),
            ),
        )
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"MONITOR_NODES is not valid JSON: {error}") from error
    if not isinstance(entries, list) or not entries:
        raise ValueError("MONITOR_NODES must be a non-empty JSON array")
    nodes: list[Node] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each MONITOR_NODES entry must be an object")
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip().rstrip("/")
        if not name or not url:
            raise ValueError("each MONITOR_NODES entry needs a non-empty name and url")
        if name in seen:
            # Two nodes under one name would interleave into a single cursor and silently
            # corrupt both nodes' ingestion, so this is fatal rather than deduplicated.
            raise ValueError(f"duplicate node name in MONITOR_NODES: {name!r}")
        seen.add(name)
        nodes.append(Node(name=name, url=url, secret=str(entry.get("secret", ""))))
    return tuple(nodes)


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
    service_admin_secret: str = os.getenv("HCAPTCHA_ADMIN_SECRET", "")
    service_probe_timeout_seconds: float = float(
        os.getenv("HCAPTCHA_PROBE_TIMEOUT_SECONDS", "1")
    )
    cors_origins: tuple[str, ...] = _origins()
    nodes: tuple[Node, ...] = _nodes()
    #: Lines requested per node per poll. The endpoint caps this at 5000 and also enforces a
    #: byte ceiling, so raising it past that does nothing; lowering it lengthens catch-up
    #: after an outage without reducing steady-state cost, since the cursor resumes exactly.
    ingest_batch_lines: int = int(os.getenv("MONITOR_INGEST_BATCH_LINES", "2000"))
    #: Per-request timeout when pulling from a node. Deliberately short: a node that is slow
    #: or unreachable must not stall the sync loop for the nodes that are healthy.
    ingest_timeout_seconds: float = float(os.getenv("MONITOR_INGEST_TIMEOUT_SECONDS", "10"))
    #: How many batches one sync pass may pull from a single node before moving on. Bounds a
    #: catch-up burst so one backlogged node cannot monopolise the loop.
    ingest_max_batches: int = int(os.getenv("MONITOR_INGEST_MAX_BATCHES", "20"))

    @property
    def service_database(self) -> Path:
        return self.hcaptcha_root / "data" / "service.db"
