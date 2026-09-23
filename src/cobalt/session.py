"""Conversation persistence between separate CLI invocations."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / ".cobalt" / "sessions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return "session-" + uuid.uuid4().hex[:12]

    def save(self, session_id: str, messages: list[dict[str, Any]], observations: dict) -> Path:
        if not session_id.startswith("session-") or not session_id[8:].isalnum():
            raise ValueError("invalid session id")
        data = {
            "schema": 1,
            "workspace": str(self.root),
            "messages": messages,
            "observations": observations,
        }
        target = self.directory / (session_id + ".json")
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".session-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def load(self, session_id: str) -> tuple[list[dict[str, Any]], dict]:
        if not session_id.startswith("session-") or not session_id[8:].isalnum():
            raise ValueError("invalid session id")
        path = self.directory / (session_id + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("workspace") != str(self.root):
            raise ValueError("session schema or workspace does not match")
        messages = data.get("messages")
        observations = data.get("observations")
        if not isinstance(messages, list) or not isinstance(observations, dict):
            raise TypeError("invalid session contents")
        return messages, observations

    def latest(self) -> str | None:
        files = sorted(self.directory.glob("session-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return files[0].stem if files else None
