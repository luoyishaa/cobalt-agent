"""Tool definitions and one execution gate for the agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .domain import ToolOutcome
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


S = lambda description: {"type": "string", "description": description}
I = lambda description: {"type": "integer", "description": description}

TOOL_SCHEMAS = [
    _tool("list_files", "List a bounded view of the repository tree.", {"path": S("Relative directory; default '.'")}, []),
    _tool("read_file", "Read numbered lines and get a content SHA-256 for safe edits.", {
        "path": S("Relative file path"), "start": I("First line number"), "lines": I("Number of lines, up to 400")}, ["path"]),
    _tool("search", "Find literal text in repository files.", {"query": S("Text to find")}, ["query"]),
    _tool("replace_text", "Replace one exact block in a file after checking its SHA-256.", {
        "path": S("Relative file path"), "old": S("Exact old text"), "new": S("Replacement text"),
        "expected_sha256": S("SHA-256 returned by read_file")}, ["path", "old", "new", "expected_sha256"]),
    _tool("create_file", "Create a new file without overwriting any existing file.", {
        "path": S("Relative new file path"), "content": S("Full file content")}, ["path", "content"]),
    _tool("run_command", "Run an argv command in the repository root without a shell. Requires approval.", {
        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "timeout": I("Seconds, 1 to 120")}, ["argv"]),
]

RISKY = {"replace_text", "create_file", "run_command"}


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
        if name not in {item["function"]["name"] for item in TOOL_SCHEMAS}:
            return ToolOutcome("error", f"unknown tool: {name}")
        if not isinstance(args, dict):
            return ToolOutcome("error", "tool arguments must be an object")
        if self.read_only and name in RISKY:
            return ToolOutcome("denied", "read-only mode does not allow this tool")
        if name in RISKY and not self.approve(name, args):
            return ToolOutcome("denied", "action was not approved")
        try:
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
