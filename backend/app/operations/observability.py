import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import settings

SAFE_FIELDS = {"request_id", "method", "path", "status_code", "duration_ms", "stack"}


class OperationsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if settings.json_logs:
            entry: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "event": getattr(record, "event_name", "application"),
            }
            for key in SAFE_FIELDS:
                if hasattr(record, key):
                    entry[key] = getattr(record, key)
            return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        request_id = getattr(record, "request_id", "-")
        status = getattr(record, "status_code", "-")
        return f"{datetime.now(UTC).isoformat()} {record.levelname} {getattr(record, 'event_name', 'application')} request_id={request_id} status={status}"


logger = logging.getLogger("dentacare.operations")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(OperationsFormatter())
    logger.addHandler(handler)


def log_event(level: int, event_name: str, **fields: Any) -> None:
    safe = {key: value for key, value in fields.items() if key in SAFE_FIELDS}
    logger.log(level, event_name, extra={"event_name": event_name, **safe})
