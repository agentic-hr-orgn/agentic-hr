"""
Structured JSON Logging Formatter and Setup for Agentic HR Core.
Outputs machine-readable single-line JSON log events tagged with request_id.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.middleware import request_id_ctx


class JSONLogFormatter(logging.Formatter):
    """
    Custom logging formatter producing structured JSON output.
    Automatically enriches logs with contextual request_id from contextvars.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(None),
        }

        # Include structured extra fields if provided
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            log_entry.update(extra_fields)

        # Include exception traceback if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def setup_logging(log_level: str = "INFO") -> None:
    """Configures the root and application loggers to emit structured JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONLogFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove default handlers to avoid duplicate log lines
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)

    # Adjust third-party chatty loggers
    logging.getLogger("uvicorn.access").handlers = [handler]
    logging.getLogger("uvicorn.access").propagate = False
