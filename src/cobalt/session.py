"""Conversation persistence between separate CLI invocations."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any


def close_interrupted_calls(messages: list[dict[str, Any]]) -> list[str]:
    """Close missing tool replies without guessing whether their effects happened."""
    pending: dict[str, str] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    pending[call_id] = call.get("function", {}).get("name", "unknown")
        elif message.get("role") == "tool":
            pending.pop(message.get("tool_call_id"), None)
    for call_id, name in pending.items():
        messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": (
                f"status: interrupted\ntool: {name}\nExecution outcome is unknown after a process interruption. "
                "Inspect the current workspace before relying on this action. Do not assume it failed or repeat it automatically."
            ),
        })
    return list(pending)


def uninspected_interrupted_calls(messages: list[dict[str, Any]], resolved: set[str]) -> list[str]:
    """An inspection counts only after its result was visible to a model turn."""
    return sorted({message["tool_call_id"] for message in messages
                   if message.get("role") == "tool"
                   and str(message.get("content", "")).startswith("status: interrupted\n")
                   and message.get("tool_call_id") not in resolved})


def interrupted_commands(messages: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Inspection does not establish whether an unknown command is safe to repeat."""
    interrupted = set(uninspected_interrupted_calls(messages, set()))
    commands = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            if call.get("id") not in interrupted or function.get("name") != "run_command":
                continue
            try:
                args = json.loads(function.get("arguments", "{}"))
            except (TypeError, ValueError):
                continue
            argv = args.get("argv") if isinstance(args, dict) else None
            if isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv):
                commands[call["id"]] = argv
    return commands


def recovery_inspection_call_ids(messages: list[dict[str, Any]], unresolved: list[str]) -> set[str]:
    if not unresolved:
        return set()
    names = {call["id"]: call.get("function", {}).get("name", "")
             for message in messages if message.get("role") == "assistant"
             for call in message.get("tool_calls") or []}
    last_interrupt = max(index for index, message in enumerate(messages)
                         if message.get("role") == "tool" and
                         message.get("tool_call_id") in unresolved and
                         str(message.get("content", "")).startswith("status: interrupted\n"))
    return {message["tool_call_id"] for message in messages[last_interrupt + 1:]
            if message.get("role") == "tool" and
            str(message.get("content", "")).startswith("status: ok\n") and
            names.get(message.get("tool_call_id")) in {"list_files", "read_file", "search"}}


class SessionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / ".cobalt" / "sessions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return "session-" + uuid.uuid4().hex[:12]

    def save(self, session_id: str, messages: list[dict[str, Any]], observations: dict,
             *, recovery_resolved: set[str] | None = None) -> Path:
        if not session_id.startswith("session-") or not session_id[8:].isalnum():
            raise ValueError("invalid session id")
        data = {
            "schema": 1,
            "workspace": str(self.root),
            "messages": messages,
            "observations": observations,
            "recovery_resolved": sorted(recovery_resolved or set()),
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
        messages, observations, _ = self.load_state(session_id)
        return messages, observations

    def load_state(self, session_id: str) -> tuple[list[dict[str, Any]], dict, set[str]]:
        if not session_id.startswith("session-") or not session_id[8:].isalnum():
            raise ValueError("invalid session id")
        path = self.directory / (session_id + ".json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("workspace") != str(self.root):
            raise ValueError("session schema or workspace does not match")
        messages = data.get("messages")
        observations = data.get("observations")
        resolved = data.get("recovery_resolved", [])
        if (not isinstance(messages, list) or not isinstance(observations, dict)
                or not isinstance(resolved, list) or any(not isinstance(item, str) for item in resolved)):
            raise TypeError("invalid session contents")
        return messages, observations, set(resolved)

    def latest(self) -> str | None:
        files = sorted(self.directory.glob("session-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return files[0].stem if files else None
