"""Compare context policies on one real-tool transcript; no model calls."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cobalt.context import (
    DEFAULT_CONTEXT_BUDGET_CHARS,
    elide_tool_results,
    prepare_context,
    select_recent_turns,
)
from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    requirement = "Keep the public function name calculate_total unchanged. Collect diagnostics first."

    class DiagnosticModel:
        def __init__(self):
            self.turns = iter([
                ModelTurn("", tuple(ToolCall(f"log-{i}", "run_command", {
                    "argv": [sys.executable, "-c", "print('diagnostic ' * 1600)"],
                }) for i in range(5))),
                ModelTurn("Diagnostics collected."),
            ])

        def complete(self, messages, tools):
            return next(self.turns)

    with tempfile.TemporaryDirectory() as directory:
        workspace = Workspace(Path(directory))
        agent = Agent(workspace, DiagnosticModel(), ToolGate(workspace, lambda _n, _a: True))
        agent.ask(requirement)
        transcript, _ = agent.sessions.load(agent.session_id)
        transcript.append({"role": "user", "content": "Continue with the same requirements."})
        # Freeze the same system, messages, tools and budget for both policies.
        selected, dropped = select_recent_turns(transcript)
        old_view, old_reads, old_commands = elide_tool_results(selected)
        new_view, new_dropped, new_reads, new_commands = prepare_context(transcript)
        rows = []
        for name, view, removed, reads, commands in [
            ("full_transcript_reference", transcript, 0, [], []),
            ("drop_turns_then_shorten", old_view, dropped, old_reads, old_commands),
            ("shorten_before_dropping", new_view, new_dropped, new_reads, new_commands),
        ]:
            requested = [c["id"] for m in view for c in m.get("tool_calls") or []]
            returned = [m["tool_call_id"] for m in view if m["role"] == "tool"]
            characters = len(json.dumps(view, ensure_ascii=False))
            rows.append({
                "policy": name, "characters": characters,
                "within_budget": characters <= DEFAULT_CONTEXT_BUDGET_CHARS,
                "requirement_present": any(m["role"] == "user" and m["content"] == requirement for m in view),
                "dropped_turns": removed, "elided_reads": len(reads), "elided_commands": len(commands),
                "tool_pairs_intact": sorted(requested) == sorted(returned),
            })
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    report = {
        "at": datetime.now(UTC).isoformat(), "commit": commit, "working_tree_dirty": dirty,
        "kind": "deterministic_context_delivery", "model_calls": 0,
        "budget_chars": DEFAULT_CONTEXT_BUDGET_CHARS, "actual_commands": 5, "rows": rows,
        "limitations": "Tests delivery of a user requirement, not model obedience or task success. "
                        "Full transcript is an over-budget reference, not a feasible budget-matched policy.",
    }
    path = root / "benchmarks" / "results" / "context-retention.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
