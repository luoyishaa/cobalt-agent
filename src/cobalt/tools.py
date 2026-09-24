"""Tool definitions and one execution gate for the agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .domain import ToolOutcome
from .outputs import OutputStore
from .workspace import Workspace


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def string_field(description: str) -> dict:
    return {"type": "string", "description": description}


def integer_field(description: str) -> dict:
    return {"type": "integer", "description": description}

TOOL_SCHEMAS = [
    _tool("read_output", "Read saved command output without executing anything. Offsets and limits are bytes; optional literal query finds the first match at or after offset. Historical output is not current file evidence.", {
        "output_id": string_field("ID returned by run_command"),
        "offset": integer_field("Starting byte offset; default 0"),
        "limit": integer_field("Maximum returned bytes, 1..8000; default 4000"),
        "query": string_field("Optional literal UTF-8 search text, 1..256 characters")}, ["output_id"]),
    _tool("list_files", "List a bounded view of the repository tree.", {"path": string_field("Relative directory; default '.'")}, []),
    _tool("read_file", "Read numbered lines and get a content SHA-256 for safe edits.", {
        "path": string_field("Relative file path"), "start": integer_field("First line number"), "lines": integer_field("Number of lines, up to 400")}, ["path"]),
    _tool("search", "Find literal text in repository files.", {"query": string_field("Text to find")}, ["query"]),
    _tool("replace_text", "Replace one exact block in a file after checking its SHA-256.", {
        "path": string_field("Relative file path"), "old": string_field("Exact old text"), "new": string_field("Replacement text"),
        "expected_sha256": string_field("SHA-256 returned by read_file")}, ["path", "old", "new", "expected_sha256"]),
    _tool("create_file", "Create a new file without overwriting any existing file.", {
        "path": string_field("Relative new file path"), "content": string_field("Full file content")}, ["path", "content"]),
    _tool("run_command", "Run an argv command in the repository root without a shell. Requires approval.", {
        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "timeout": integer_field("Seconds, 1 to 120")}, ["argv"]),
]

RISKY = {"replace_text", "create_file", "run_command"}
SCHEMA_BY_NAME = {item["function"]["name"]: item["function"]["parameters"] for item in TOOL_SCHEMAS}


def validate_arguments(name: str, args: dict[str, Any]) -> None:
    spec = SCHEMA_BY_NAME[name]
    properties = spec["properties"]
    extra = set(args) - set(properties)
    missing = set(spec["required"]) - set(args)
    if extra or missing:
        raise ValueError(f"invalid fields; extra={sorted(extra)}, missing={sorted(missing)}")
    for key, value in args.items():
        field = properties[key]
        kind = field["type"]
        if kind == "string" and not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise TypeError(f"{key} must be an integer")
        if kind == "array" and (
            not isinstance(value, list)
            or len(value) < field.get("minItems", 0)
            or any(not isinstance(item, str) for item in value)
        ):
            raise TypeError(f"{key} must be a non-empty list of strings")


class ToolGate:
    def __init__(self, workspace: Workspace, approve: Callable[[str, dict[str, Any]], bool], *, read_only: bool = False):
        self.workspace = workspace
        self.approve = approve
        self.read_only = read_only

    def schemas(self) -> list[dict[str, Any]]:
        if self.read_only:
            return [schema for schema in TOOL_SCHEMAS if schema["function"]["name"] not in RISKY]
        return TOOL_SCHEMAS

    def execute(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        if name not in SCHEMA_BY_NAME:
            return ToolOutcome("error", f"unknown tool: {name}")
        if not isinstance(args, dict):
            return ToolOutcome("error", "tool arguments must be an object")
        if self.read_only and name in RISKY:
            return ToolOutcome("denied", "read-only mode does not allow this tool")
        try:
            validate_arguments(name, args)
        except (TypeError, ValueError) as exc:
            return ToolOutcome("error", str(exc))
        if name in RISKY and not self.approve(name, args):
            return ToolOutcome("denied", "action was not approved")
        before = self.workspace.snapshot() if name in RISKY else None
        outcome = self._execute(name, args)
        if before is None:
            return outcome
        after = self.workspace.snapshot()
        changes = after.changes_from(before)
        return replace(outcome, changes=changes, workspace_fingerprint=after.fingerprint,
                       observation_errors=before.errors + after.errors,
                       changed=outcome.changed or bool(changes),
                       verified=outcome.verified and before.fingerprint is not None
                       and before.fingerprint == after.fingerprint)

    def _execute(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        try:
            if name == "read_output":
                return OutputStore(self.workspace.root).read(
                    args["output_id"], offset=args.get("offset", 0), limit=args.get("limit", 4000),
                    query=args.get("query"),
                )
            if name == "list_files":
                return self.workspace.list_files(str(args.get("path", ".")))
            if name == "read_file":
                return self.workspace.read_file(
                    str(args["path"]), start=int(args.get("start", 1)), lines=int(args.get("lines", 160))
                )
            if name == "search":
                return self.workspace.search(str(args["query"]))
            if name == "replace_text":
                return self.workspace.replace_text(
                    str(args["path"]), str(args["old"]), str(args["new"]), str(args["expected_sha256"])
                )
            if name == "create_file":
                return self.workspace.create_file(str(args["path"]), str(args["content"]))
            if name == "run_command":
                argv = args["argv"]
                if not isinstance(argv, list):
                    raise ValueError("argv must be a list")
                return self.workspace.run_command(argv, timeout=int(args.get("timeout", 60)))
        except (KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            return ToolOutcome("error", str(exc))
        return ToolOutcome("error", "tool is unavailable")
