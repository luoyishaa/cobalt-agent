"""Human-facing command line for repository conversations."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .engine import Agent
from .model import from_config
from .model_config import resolve_config
from .session import SessionStore
from .tools import ToolGate
from .workspace import Workspace


def _approval(auto: bool):
    def approve(name: str, args: dict) -> bool:
        if auto:
            return True
        detail = args.get("path") or args.get("argv") or ""
        if name == "replace_text":
            preview = difflib.unified_diff(
                str(args.get("old", "")).splitlines(), str(args.get("new", "")).splitlines(),
                fromfile="current block", tofile="proposed block", lineterm="",
            )
            print("\n".join(preview))
        elif name == "create_file":
            print(str(args.get("content", ""))[:4000])
        try:
            answer = input(f"Allow {name} ({detail})? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    return approve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cobalt: a local coding agent with visible verification")
    parser.add_argument("question", nargs="*", help="One task; omit for interactive mode")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--provider", help="Provider preset; overrides COBALT_PROVIDER")
    parser.add_argument("--model", help="Exact model ID; overrides COBALT_MODEL_ID")
    parser.add_argument("--base-url", help="HTTPS API base URL; overrides COBALT_BASE_URL")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env",
                        help="Local credentials file (default: .env in the launch directory)")
    parser.add_argument("--max-tool-calls", type=int, default=16)
    parser.add_argument("--mode", choices=("ask", "code"), default="code", help="Ask is read-only; code can request edits")
    parser.add_argument("--resume", default=None, help="Session id or 'latest' from this workspace")
    parser.add_argument("--yes", action="store_true", help="Approve file edits and commands without asking")
    args = parser.parse_args(argv)
    if args.max_tool_calls < 1:
        parser.error("--max-tool-calls must be positive")
    try:
        workspace = Workspace(args.workspace)
        config = resolve_config(env_file=args.env_file, provider=args.provider,
                                model=args.model, base_url=args.base_url)
        model = from_config(config)
    except (ValueError, OSError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2
    resume = args.resume
    if resume == "latest":
        resume = SessionStore(workspace.root).latest()
        if not resume:
            print("No session to resume", file=sys.stderr)
            return 2
    try:
        agent = Agent(
            workspace, model, ToolGate(workspace, _approval(args.yes), read_only=args.mode == "ask"),
            max_tool_calls=args.max_tool_calls, resume=resume,
        )
    except (ValueError, OSError) as exc:
        print(f"Session error: {exc}", file=sys.stderr)
        return 2

    def ask(question: str) -> None:
        result = agent.ask(question)
        print(result.answer)
        print(f"\n[{result.status}] session: {result.session_id} run: {workspace.root / '.cobalt' / 'runs' / result.run_id}")

    if args.question:
        ask(" ".join(args.question))
        return 0
    print(f"Cobalt in {workspace.root}. Type /exit to stop.")
    while True:
        try:
            question = input("cobalt> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question in {"/exit", "/quit"}:
            break
        if question:
            ask(question)
    return 0
