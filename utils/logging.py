"""Human-readable logging for the prospector.

Log lines look like: [DISCOVERY] Found @example
Never logs secret values (API keys are redacted if they ever appear).
"""
from __future__ import annotations

import logging
import re
import sys

from config import LOGS_DIR

_SECRET_PATTERN = re.compile(r"(key|token|secret)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{8,})", re.IGNORECASE)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***REDACTED***", msg)


def get_logger(name: str = "fableya") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.INFO)

    fmt = RedactingFormatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(LOGS_DIR / "prospector.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def tag(category: str, message: str) -> str:
    return f"[{category}] {message}"
