"""Bounded conversation context and file evidence that expires with the file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .workspace import Workspace, digest_bytes


def select_recent_turns(messages: list[dict[str, Any]], budget_chars: int = 48_000) -> tuple[list[dict[str, Any]], int]:
    """Drop only whole completed user turns; never orphan a tool response."""
    if not messages or messages[0].get("role") != "system":
        raise ValueError("conversation must start with a system message")
    starts = [i for i, message in enumerate(messages) if message.get("role") == "user"]
    if not starts:
        return list(messages), 0
    groups = [messages[start : starts[n + 1] if n + 1 < len(starts) else len(messages)] for n, start in enumerate(starts)]
    chosen: list[list[dict[str, Any]]] = []
    used = len(json.dumps(messages[0], ensure_ascii=False))
    for group in reversed(groups):
        size = sum(len(json.dumps(message, ensure_ascii=False)) for message in group)
        if chosen and used + size > budget_chars:
            break
        chosen.append(group)
        used += size
    chosen.reverse()
    selected = [messages[0], *(message for group in chosen for message in group)]
    return selected, len(groups) - len(chosen)


class EvidenceBook:
    def __init__(self, observations: dict[str, dict[str, str]] | None = None):
        self.observations = dict(observations or {})

    def observe(self, path: str, digest: str, excerpt: str) -> None:
        self.observations.pop(path, None)
        self.observations[path] = {"sha256": digest, "excerpt": excerpt[:500]}
        while len(self.observations) > 12:
            del self.observations[next(iter(self.observations))]

    def context(self, workspace: Workspace) -> str:
        notes: list[str] = []
        for relative, item in list(self.observations.items())[-4:]:
            try:
                path: Path = workspace._path(relative, must_exist=True)
                fresh = digest_bytes(path.read_bytes()) == item["sha256"]
            except (OSError, ValueError):
                fresh = False
            if fresh:
                notes.append(f"Fresh observed file {relative} ({item['sha256']}): {item['excerpt']}")
            else:
                notes.append(f"Observed file {relative} changed; read it again before relying on old content.")
        return "\n".join(notes)
