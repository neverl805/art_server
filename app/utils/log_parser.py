"""Parser for the current Safari hCaptcha Loguru text format."""

from __future__ import annotations

import json
import re
from datetime import datetime

from app.models.log import LogEntry, LogLevel


class LogParser:
    LOG_PATTERN = re.compile(
        r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)"
        r"\s+\|\s+(?P<level>[A-Z]+)\s+\|\s+"
        r"ip=(?P<ip>\S+) session=(?P<session_id>\S+) request=(?P<request_id>\S+)"
        r"\s+\|\s+(?P<module>[\w.]+):(?P<function>[^:]+):(?P<line>\d+)"
        r"\s+\|\s+(?P<message>.*)$"
    )

    EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "request_payload",
            re.compile(r"^request payload=(?P<payload>\{.*\})$"),
        ),
        (
            "response_payload",
            re.compile(r"^response payload=(?P<payload>\{.*\})$"),
        ),
        (
            "hcaptcha_trace",
            re.compile(r"^hCaptcha trace payload=(?P<payload>\{.*\})$"),
        ),
        (
            "request_started",
            re.compile(r"^request started method=(?P<method>\S+) path=(?P<path>\S+)$"),
        ),
        (
            "request_completed",
            re.compile(
                r"^request completed method=(?P<method>\S+) path=(?P<path>\S+) "
                r"status=(?P<status>\d+) elapsed_ms=(?P<elapsed_ms>[\d.]+)$"
            ),
        ),
        (
            "solver_queue",
            re.compile(r"^solver slot acquired queue_ms=(?P<queue_ms>[\d.]+)$"),
        ),
        (
            "solve_succeeded",
            re.compile(
                r"^hCaptcha solved host=(?P<host>\S+) attempt=(?P<attempt>\d+) "
                r"elapsed=(?P<elapsed>[\d.]+)s requests=(?P<requests>\d+) "
                r"direct=(?P<direct>True|False)$"
            ),
        ),
        (
            "solve_failed",
            re.compile(
                r"^hCaptcha failed host=(?P<host>\S+) elapsed=(?P<elapsed>[\d.]+)s "
                r"attempts=(?P<attempts>\d+)$"
            ),
        ),
        (
            "solve_attempt_failed",
            re.compile(
                r"^hCaptcha attempt failed host=(?P<host>\S+) "
                r"attempt=(?P<attempt>\d+)/(?P<attempts>\d+) error=(?P<error>.*)$"
            ),
        ),
        (
            "token_reserved",
            re.compile(
                r"^token reserved hint=(?P<token_hint>\S+) remaining=(?P<remaining>\d+) "
                r"pending=(?P<pending>\d+)$"
            ),
        ),
        (
            "token_committed",
            re.compile(
                r"^token usage committed hint=(?P<token_hint>\S+) "
                r"remaining=(?P<remaining>\d+) used=(?P<used>\d+)$"
            ),
        ),
        (
            "token_refunded",
            re.compile(
                r"^token usage refunded(?: after exception)? hint=(?P<token_hint>\S+) "
                r"remaining=(?P<remaining>\d+)(?: error=(?P<error>.*))?$"
            ),
        ),
        (
            "token_rejected",
            re.compile(r"^token rejected reason=(?P<reason>\S+)$"),
        ),
        ("validation_failed", re.compile(r"^request validation failed errors=(?P<error>.*)$")),
        ("unhandled_error", re.compile(r"^unhandled request error: (?P<error>.*)$")),
        ("service_started", re.compile(r"^service started (?P<details>.*)$")),
        ("service_stopped", re.compile(r"^service stopped$")),
    )

    @classmethod
    def parse_line(cls, line: str) -> LogEntry | None:
        raw_line = line.rstrip("\r\n")
        match = cls.LOG_PATTERN.match(raw_line)
        if match is None:
            return None
        data = match.groupdict()
        try:
            level = LogLevel(data["level"])
        except ValueError:
            level = LogLevel.INFO
        event = "log"
        attributes: dict[str, object] = {}
        for event_name, pattern in cls.EVENT_PATTERNS:
            event_match = pattern.match(data["message"])
            if event_match is not None:
                event = event_name
                attributes = cls._normalize_attributes(event_match.groupdict())
                break
        return LogEntry(
            ip=data["ip"],
            session_id=data["session_id"],
            timestamp=datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S.%f"),
            request_id=data["request_id"],
            level=level,
            module=data["module"],
            function=data["function"],
            line=int(data["line"]),
            event=event,
            message=data["message"],
            attributes=attributes,
            raw_line=raw_line,
        )

    @staticmethod
    def _normalize_attributes(values: dict[str, str | None]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        integer_keys = {
            "status",
            "attempt",
            "attempts",
            "requests",
            "remaining",
            "pending",
            "used",
        }
        float_keys = {"elapsed", "elapsed_ms", "queue_ms"}
        for key, value in values.items():
            if value is None:
                continue
            if key in integer_keys:
                normalized[key] = int(value)
            elif key in float_keys:
                normalized[key] = float(value)
            elif key == "direct":
                normalized[key] = value == "True"
            elif key == "payload":
                try:
                    payload = json.loads(value)
                except json.JSONDecodeError:
                    normalized[key] = value
                else:
                    normalized[key] = payload
            else:
                normalized[key] = value
        return normalized
