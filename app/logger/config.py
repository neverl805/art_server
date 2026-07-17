"""Loguru configuration for the monitoring API itself."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_dir: Path | None = None) -> None:
    target = log_dir or Path(__file__).resolve().parents[2] / "logs"
    target.mkdir(parents=True, exist_ok=True)
    logger.remove()
    log_format = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    )
    logger.add(sys.stdout, level="INFO", format=log_format, colorize=sys.stdout.isatty())
    logger.add(
        str(target / "monitor_{time:YYYY-MM-DD}.log"),
        level="INFO",
        format=log_format,
        rotation="00:00",
        retention="14 days",
        compression="zip",
        enqueue=True,
        encoding="utf-8",
    )
