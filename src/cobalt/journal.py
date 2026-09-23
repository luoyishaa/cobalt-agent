"""Append-only run evidence, with a compact result for people and scripts."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .domain import RunResult


class Journal:
    def __init__(self, root: Path, run_id: str):
        self.directory = root / ".cobalt" / "runs" / run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events_path = self.directory / "events.jsonl"

    def add(self, kind: str, **data: Any) -> None:
        event = {
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            **data,
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def finish(self, result: RunResult) -> Path:
        path = self.directory / "result.json"
        path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.add("run_finished", status=result.status, tool_calls=result.tool_calls)
        return path
