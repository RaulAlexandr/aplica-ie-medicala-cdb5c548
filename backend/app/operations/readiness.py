import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text


async def check_readiness(session_factory: Any, document_dir: str | Path | None) -> dict[str, str]:
    result = {"database": "unavailable", "documents": "unavailable"}
    try:
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:  # noqa: BLE001,S110 - readiness must report dependency failure without details
        pass
    if document_dir is None:
        result["documents"] = "not_configured"
        return result
    directory = Path(document_dir)
    try:
        if not directory.is_dir():
            return result
        with tempfile.NamedTemporaryFile(prefix=".dentacare-ready-", dir=directory, delete=False) as probe:
            probe_path = Path(probe.name)
            probe.write(b"dentacare-readiness-probe")
            probe.flush()
            os.fsync(probe.fileno())
        try:
            result["documents"] = "ok" if probe_path.read_bytes() == b"dentacare-readiness-probe" else "unavailable"
        finally:
            probe_path.unlink(missing_ok=True)
    except OSError:
        result["documents"] = "unavailable"
    return result
