"""Bounded conversation context and file evidence that expires with the file."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .workspace import Workspace

DEFAULT_CONTEXT_BUDGET_CHARS = 48_000
REPEATABLE_READ_TOOLS = {"list_files", "read_file", "search"}

def select_recent_turns(
    messages: list[dict[str, Any]], budget_chars: int = DEFAULT_CONTEXT_BUDGET_CHARS,
) -> tuple[list[dict[str, Any]], int]:
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


def elide_tool_results(
    messages: list[dict[str, Any]], budget_chars: int = DEFAULT_CONTEXT_BUDGET_CHARS,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Bound the model view without changing the durable transcript or tool pairing."""
    view = [dict(message) for message in messages]
    call_names = {
        call["id"]: call.get("function", {}).get("name")
        for message in view if message.get("role") == "assistant"
        for call in message.get("tool_calls") or [] if isinstance(call.get("id"), str)
    }
    elided_reads: list[str] = []
    elided_commands: list[str] = []
    for message in view:
        if len(json.dumps(view, ensure_ascii=False)) <= budget_chars:
            break
        call_id = message.get("tool_call_id")
        if message.get("role") != "tool" or call_names.get(call_id) not in REPEATABLE_READ_TOOLS:
            continue
        marker = (
            "status: elided\nEarlier read result omitted from this model request to fit the context budget. "
            "The original is retained in the session. Read the source again if its details matter."
        )
        if len(message.get("content", "")) <= len(marker):
            continue
        message["content"] = marker
        elided_reads.append(call_id)
    # A successful command's exit status is durable evidence, but its stdout is
    # not safe to recreate: the command may have changed external state.
    command_results = [message for message in view if message.get("role") == "tool"
                       and call_names.get(message.get("tool_call_id")) == "run_command"]
    for message in command_results[:-1]:
        if len(json.dumps(view, ensure_ascii=False)) <= budget_chars:
            break
        content = str(message.get("content", ""))
        if not content.startswith("status: ok\nexit_code: 0\n"):
            continue
        raw_output = content.split("\n", 2)[2]
        marker = (
            "status: elided\ntool: run_command\nexit_code: 0\n"
            f"result_chars: {len(content)}\nresult_sha256: {sha256(content.encode('utf-8')).hexdigest()}\n"
            "Raw output prefix (not a summary):\n" + raw_output[:240] +
            "\n[... middle omitted ...]\nRaw output suffix (not a summary):\n" + raw_output[-240:] +
            "\nThe full result remains in the session. Do not rerun this command merely to recover "
            "omitted output; inspect current files or use a new safe check."
        )
        if len(content) <= len(marker):
            continue
        message["content"] = marker
        elided_commands.append(message["tool_call_id"])
    return view, elided_reads, elided_commands


class EvidenceBook:
    def __init__(self, observations: dict[str, dict[str, Any]] | None = None):
        self.observations = dict(observations or {})

    def observe(self, path: str, digest: str, *, start: int = 1, lines: int = 160) -> None:
        previous = self.observations.get(path)
        ranges = list(previous.get("ranges", [])) if previous and previous.get("sha256") == digest else []
        span = [start, start + lines - 1]
        if span not in ranges:
            ranges.append(span)
        self.observations.pop(path, None)
        self.observations[path] = {"sha256": digest, "ranges": ranges[-4:]}
        while len(self.observations) > 12:
            del self.observations[next(iter(self.observations))]

    def context(self, workspace: Workspace) -> str:
        notes: list[str] = []
        for relative, item in list(self.observations.items())[-4:]:
            try:
                fresh = workspace.file_digest(relative) == item["sha256"]
            except (OSError, ValueError):
                fresh = False
            if fresh:
                ranges = item.get("ranges", [])
                locations = ", ".join(f"{start}-{end}" for start, end in ranges)
                hint = f"; requested lines {locations}" if locations else ""
                notes.append(
                    f"Previously read file {relative} (sha256 {item['sha256']}{hint}). "
                    "This is a location index, not a content summary; read the file again for details."
                )
            else:
                notes.append(f"Observed file {relative} changed; read it again before relying on old content.")
        return "\n".join(notes)
