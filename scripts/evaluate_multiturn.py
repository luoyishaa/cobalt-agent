"""Paired live repairs with requirements separated from follow-ups by long logs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from cobalt.context import elide_tool_results, select_recent_turns
from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.evaluation import verifier_environment
from cobalt.model import from_config
from cobalt.model_config import resolve_config
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace

REQUIREMENT = (
    "Implement invoice.total(amounts), where amounts is a list of nonnegative integer cents. "
    "Return the integer total including 17 percent tax, rounding half up to a cent. "
    "Keep the function name and do not modify the input list. First collect diagnostics only; "
    "wait for my next message before implementing."
)
FOLLOWUPS = {
    "retain_requirement": "Now implement invoice.py using the requirements from my previous message. "
                          "Read it first, verify your change with a command, and reread it before answering.",
    "revised_requirement": "Correction: use 9 percent tax instead of 17 percent. "
                           "Now implement invoice.total(amounts) for nonnegative integer cents, "
                           "rounding half up to a cent without modifying the input list. "
                           "Read it first, verify your change with a command, and reread it before answering.",
}


class DropFirstAgent(Agent):
    """Evaluation-only baseline: same runtime, differing only in context order."""

    def _model_context(self, retry_hint=""):
        current, _, _, _ = super()._model_context(retry_hint)
        original = list(self.messages)
        original[0] = current[0]  # Identical dynamic system instructions in both arms.
        selected, dropped = select_recent_turns(original)
        view, reads, commands = elide_tool_results(selected)
        return view, dropped, reads, commands


def verify(root: Path, rate: int) -> dict:
    # The oracle is outside the agent workspace and uses integer arithmetic.
    # Testing the returned values separately from runtime status catches false completion.
    program = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('candidate', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rate = int(sys.argv[2])
cases = [[], [0], [10000], [199,201], [1], [50], [49,51,99], [10**12,13]]
rows = []
for values in cases:
    original = list(values)
    expected = (sum(values)*(100+rate)+50)//100
    try:
        actual = module.total(values)
        passed = type(actual) is int and actual == expected and values == original
        rows.append({'input': original, 'expected': expected, 'actual': repr(actual), 'passed': passed})
    except Exception as exc:
        rows.append({'input': original, 'expected': expected, 'error': type(exc).__name__, 'passed': False})
print(json.dumps(rows))
"""
    try:
        run = subprocess.run(
            [sys.executable, "-I", "-B", "-c", program, str(root / "invoice.py"), str(rate)],
            cwd=root, env=verifier_environment(root), capture_output=True, text=True, timeout=10, check=False,
        )
        rows = json.loads(run.stdout) if run.returncode == 0 else []
        return {"passed": bool(rows) and all(row["passed"] for row in rows),
                "checks": rows, "exit_code": run.returncode, "stderr": run.stderr[-1000:]}
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return {"passed": False, "error": type(exc).__name__}


def run_attempt(config, case: str, policy: str, attempt: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="cobalt-multiturn-") as directory:
        root = Path(directory)
        (root / "invoice.py").write_text("def total(amounts):\n    raise NotImplementedError\n", encoding="utf-8")
        workspace = Workspace(root)
        rate = 17 if case == "retain_requirement" else 9
        baseline = verify(root, rate)
        if baseline["passed"]:
            raise ValueError("fixture must fail before repair")

        class Diagnostics:
            def __init__(self):
                self.turns = iter([
                    ModelTurn("", tuple(ToolCall(f"log-{i}", "run_command", {
                        "argv": [sys.executable, "-c", "print('diagnostic ' * 1600)"],
                    }) for i in range(5))),
                    ModelTurn("Diagnostics collected. Waiting for your next message."),
                ])

            def complete(self, messages, tools):
                return next(self.turns)

        cls = Agent if policy == "shorten_first" else DropFirstAgent
        agent = cls(workspace, Diagnostics(), ToolGate(workspace, lambda _n, _a: True), max_tool_calls=12)
        agent.ask(REQUIREMENT)
        agent.model = from_config(config)
        started = time.monotonic()
        result = agent.ask(FOLLOWUPS[case])
        elapsed = round(time.monotonic() - started, 3)
        verdict = verify(root, rate)
        events = [json.loads(line) for line in
                  (root / ".cobalt" / "runs" / result.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        return {
            "case": case, "policy": policy, "attempt": attempt, "expected_tax_percent": rate,
            "task_passed": verdict["passed"], "run_status": result.status,
            "runtime_completed_without_task_success": result.status == "completed" and not verdict["passed"],
            "tool_calls": result.tool_calls, "elapsed_seconds": elapsed,
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "answer": result.answer, "source": (root / "invoice.py").read_text(encoding="utf-8"),
            "verifier": verdict, "baseline_passed": baseline["passed"],
            "contexts": [event for event in events if event["kind"] == "context_built"],
            "actions": [{"name": event["name"], "status": event["status"], "args": event["args"]}
                        for event in events if event["kind"] == "tool_finished"],
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 5:
        parser.error("repeat must be 1..5")
    root = Path(__file__).resolve().parents[1]
    config = resolve_config(env_file=root / ".env", provider="deepseek")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    if dirty:
        parser.error("commit the experiment before running")
    jobs = [(case, policy, attempt) for attempt in range(1, args.repeat + 1)
            for case in FOLLOWUPS for policy in
            (["drop_first", "shorten_first"] if attempt % 2 else ["shorten_first", "drop_first"])]
    report = {
        "at": datetime.now(UTC).isoformat(), "commit": commit, "working_tree_dirty": dirty,
        "provider": config.provider, "model": config.model,
        "requirements": REQUIREMENT, "followups": FOLLOWUPS,
        "repeats": args.repeat, "budget_chars": 48000, "max_tool_calls": 12,
        "protocol": "Scripted setup with five real commands; live model performs each repair. "
                    "Same fixture, tools, model and budgets. Two workers; no temperature/seed override. "
                    "Measures a deliberately context-dependent small task, not general coding success.",
        "rows": [],
    }
    output = root / "benchmarks" / "results" / ("multiturn-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".json")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_attempt, config, *job) for job in jobs]
        for job, future in zip(jobs, futures, strict=True):
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - preserve other attempts on infrastructure failure
                row = {"case": job[0], "policy": job[1], "attempt": job[2],
                       "task_passed": False, "infrastructure_error": type(exc).__name__}
            report["rows"].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{job}: passed={row['task_passed']} status={row.get('run_status')} tools={row.get('tool_calls')}", flush=True)
    print(output, flush=True)


if __name__ == "__main__":
    main()
