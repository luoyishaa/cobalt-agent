"""Bounded conversation context and file evidence that expires with the file."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .workspace import Workspace

DEFAULT_CONTEXT_BUDGET_CHARS = 48_000
REPEATABLE_READ_TOOLS = {"list_files", "read_file", "search", "read_output"}

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
    # Older archived logs yield space before small source reads do.
    # The latest output stays visible; executed commands are never recreated.
    command_results = [message for message in view if message.get("role") == "tool"
                       and call_names.get(message.get("tool_call_id")) == "run_command"]
    for message in command_results[:-1]:
        if len(json.dumps(view, ensure_ascii=False)) <= budget_chars:
            break
        content = str(message.get("content", ""))
        header = content.splitlines()
        locator = next((line for line in header[:4] if line.startswith("output_id: ")), "")
        legacy_success = content.startswith("status: ok\nexit_code: 0\n")
        if len(header) < 2 or not header[1].startswith("exit_code: ") or not (locator or legacy_success):
            continue
        raw_output = content.split("\n", 2)[2]
        marker = (
            "status: elided\ntool: run_command\n" + header[1] + "\n"
            + "original_" + header[0] + "\n"
            + (locator + "\n" if locator else "") +
            f"result_chars: {len(content)}\nresult_sha256: {sha256(content.encode('utf-8')).hexdigest()}\n"
            "Raw output prefix (not a summary):\n" + raw_output[:240] +
            "\n[... middle omitted ...]\nRaw output suffix (not a summary):\n" + raw_output[-240:] +
            "\nDo not rerun this command merely to recover omitted output. "
            + ("Use read_output with the output_id for the saved original."
               if locator else "Only the captured result remains in the session; inspect current files if needed.")
        )
        if len(content) <= len(marker):
            continue
        message["content"] = marker
        elided_commands.append(message["tool_call_id"])
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
        if call_names.get(call_id) == "read_output":
            locator = next((line for line in str(message.get("content", "")).splitlines()[:4]
                            if line.startswith("output_id: ")), "")
            marker = "status: elided\n" + locator + "\nSaved output page omitted. Use read_output to retrieve it again."
        if len(message.get("content", "")) <= len(marker):
            continue
        message["content"] = marker
        elided_reads.append(call_id)
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
