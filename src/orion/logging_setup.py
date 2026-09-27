"""Structured, run-scoped logging.

Every log line is a JSON object carrying the run_id. Lines go to stderr for a
human watching the run, and to runs/<run_id>/trace.jsonl as the durable audit
trail. That file is what a regulator-facing reviewer would actually read.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class RunLogger:
    def __init__(self, run_id: str, run_dir: Path, echo: bool = True) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.echo = echo
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._trace_path = run_dir / "trace.jsonl"
        self._fh = self._trace_path.open("a", encoding="utf-8")
        # The assess stage fans out across threads; without this the trace
        # file and the console interleave mid-line and stop being readable.
        self._lock = threading.Lock()

    def log(self, event: str, level: str = "info", **fields: Any) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "run_id": self.run_id,
            "level": level,
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            if self.echo:
                marker = {"info": "  ", "warn": "! ", "error": "X "}.get(level, "  ")
                detail = " ".join(f"{k}={v}" for k, v in fields.items() if k != "traceback")
                print(f"{marker}{event:<28} {detail}", file=sys.stderr)

    def warn(self, event: str, **fields: Any) -> None:
        self.log(event, level="warn", **fields)

    def error(self, event: str, **fields: Any) -> None:
        self.log(event, level="error", **fields)

    @contextmanager
    def stage(self, name: str) -> Iterator[dict[str, Any]]:
        """Time a stage and record its duration for the audit record."""
        started = time.perf_counter()
        self.log("stage.start", stage=name)
        bucket: dict[str, Any] = {}
        try:
            yield bucket
        except Exception as exc:  # noqa: BLE001 - we re-raise after logging
            elapsed = (time.perf_counter() - started) * 1000
            self.error("stage.failed", stage=name, ms=round(elapsed, 1), error=repr(exc))
            raise
        else:
            elapsed = (time.perf_counter() - started) * 1000
            bucket["elapsed_ms"] = round(elapsed, 1)
            self.log("stage.done", stage=name, ms=round(elapsed, 1), **bucket)

    def close(self) -> None:
        self._fh.close()
