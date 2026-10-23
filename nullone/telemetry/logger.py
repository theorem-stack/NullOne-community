from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import orjson


class ReleaseLogger:
    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        event_id = record.get("event_id", "unknown")
        path = self._log_dir / f"{event_id}_{ts}.json"
        path.write_bytes(orjson.dumps(record, option=orjson.OPT_INDENT_2) + b"\n")
        print(orjson.dumps(record).decode("utf-8"))
        return path
