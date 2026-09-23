"""Fresh-workspace evaluation with separate task and runtime outcomes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .engine import Agent
from .model import Model
from .tools import ToolGate
from .workspace import Workspace


def run_case(case: dict[str, Any], fixtures_root: Path, model_factory: Callable[[], Model]) -> dict[str, Any]:
    source = (fixtures_root / case["fixture"]).resolve()
    if not source.is_dir() or not source.is_relative_to(fixtures_root.resolve()):
        raise ValueError("case fixture is missing or outside fixture root")
    with tempfile.TemporaryDirectory(prefix="cobalt-eval-") as directory:
        workspace_path = Path(directory) / "repo"
        shutil.copytree(source, workspace_path)
        workspace = Workspace(workspace_path)
        agent = Agent(workspace, model_factory(), ToolGate(workspace, lambda _name, _args: True), max_tool_calls=12)
        started = time.monotonic()
        result = agent.ask(case["request"])
        elapsed = round(time.monotonic() - started, 3)
        event_path = workspace_path / ".cobalt" / "runs" / result.run_id / "events.jsonl"
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        tool_names = [event["name"] for event in events if event["kind"] == "tool_finished"]
        steps = [
            {
                "name": event["name"],
                "status": event["status"],
                "path": event.get("path"),
                "changed": event["changed"],
                "output_excerpt": event.get("output", "")[:300],
            }
            for event in events if event["kind"] == "tool_finished"
        ]
        verifier_exit = None
        if case["kind"] == "repair":
            command = [sys.executable if item == "python" else item for item in case["verifier"]]
            verified = subprocess.run(command, cwd=workspace_path, capture_output=True, text=True, timeout=30, check=False)
            verifier_exit = verified.returncode
            passed = verifier_exit == 0 and bool(result.changed_paths)
        elif case["kind"] == "question":
            passed = "read_file" in tool_names and all(
                term.casefold() in result.answer.casefold() for term in case["answer_terms"]
            ) and not any(
                term.casefold() in result.answer.casefold() for term in case.get("forbidden_terms", [])
            )
        else:
            raise ValueError("unknown case kind")
        failure_category = None
        if not passed:
            if result.status == "model_error":
                failure_category = "model_error"
            elif result.status == "limit":
                failure_category = "tool_limit"
            elif case["kind"] == "repair" and verifier_exit != 0:
                failure_category = "verifier_failed"
            elif case["kind"] == "repair":
                failure_category = "no_file_change"
            else:
                failure_category = "answer_or_evidence_mismatch"
        return {
            "id": case["id"],
            "kind": case["kind"],
            "passed": passed,
            "run_status": result.status,
            "tool_calls": result.tool_calls,
            "tool_names": tool_names,
            "steps": steps,
            "failure_category": failure_category,
            "changed_paths": result.changed_paths,
            "successful_commands": len(result.verified_commands),
            "verifier_exit": verifier_exit,
            "elapsed_seconds": elapsed,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "answer_excerpt": result.answer[:600],
        }
