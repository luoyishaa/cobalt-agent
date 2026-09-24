"""Fresh-workspace evaluation with separate task and runtime outcomes."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from .domain import ToolOutcome
from .engine import Agent
from .model import Model
from .tools import ToolGate
from .workspace import Workspace


def test_sources(root: Path) -> dict[str, str]:
    """Snapshot visible test inputs, excluding interpreter-generated caches."""
    tests = root / "tests"
    if not tests.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in tests.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def verifier_environment(workspace_path: Path) -> dict[str, str]:
    names = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TMP", "TEMP", "HOME", "USERPROFILE")
    env = {name: os.environ[name] for name in names if name in os.environ}
    env["PYTHONPATH"] = str(workspace_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class EvaluationGate(ToolGate):
    """A capability ablation for evaluation, not a different model or prompt."""

    def __init__(self, workspace: Workspace, output_retrieval: bool):
        super().__init__(workspace, lambda _name, _args: True)
        self.output_retrieval = output_retrieval

    def schemas(self):
        return [schema for schema in super().schemas()
                if self.output_retrieval or schema["function"]["name"] != "read_output"]

    def execute(self, name, args):
        if name == "read_output" and not self.output_retrieval:
            return ToolOutcome("denied", "Saved output retrieval is disabled for this evaluation.")
        return super().execute(name, args)


def run_case(case: dict[str, Any], fixtures_root: Path, model_factory: Callable[[], Model],
             *, output_retrieval: bool = True) -> dict[str, Any]:
    source = (fixtures_root / case["fixture"]).resolve()
    if not source.is_dir() or not source.is_relative_to(fixtures_root.resolve()):
        raise ValueError("case fixture is missing or outside fixture root")
    with tempfile.TemporaryDirectory(prefix="cobalt-eval-") as directory:
        workspace_path = Path(directory) / "repo"
        shutil.copytree(source, workspace_path)
        command: list[str] | None = None
        baseline_exit = None
        if case["kind"] == "repair":
            if "hidden_verifier" in case:
                verifier_dir = (fixtures_root / case["hidden_verifier"]).resolve()
                if not verifier_dir.is_dir() or not verifier_dir.is_relative_to(fixtures_root.resolve()):
                    raise ValueError("hidden verifier is missing or outside benchmark root")
                if not any(verifier_dir.glob("test_*.py")):
                    raise ValueError("hidden verifier has no test files")
                command = [sys.executable, "-m", "unittest", "discover", "-s", str(verifier_dir), "-q"]
            else:
                command = [sys.executable if item == "python" else item for item in case["verifier"]]
            baseline = subprocess.run(
                command, cwd=workspace_path, env=verifier_environment(workspace_path),
                capture_output=True, text=True, timeout=30, check=False,
            )
            baseline_exit = baseline.returncode
            if baseline_exit == 0:
                raise ValueError("repair case already passes its external verifier before the agent runs")
        original_tests = test_sources(workspace_path)
        workspace = Workspace(workspace_path)
        agent = Agent(workspace, model_factory(), EvaluationGate(workspace, output_retrieval), max_tool_calls=12)
        started = time.monotonic()
        result = agent.ask(case["request"])
        elapsed = round(time.monotonic() - started, 3)
        event_path = workspace_path / ".cobalt" / "runs" / result.run_id / "events.jsonl"
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        tool_names = [event["name"] for event in events if event["kind"] == "tool_finished"]
        audits = [event for event in events if event["kind"] == "answer_audited"]
        contexts = [event for event in events if event["kind"] == "context_built"]
        checked_references = audits[-1]["references"] if audits else []
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
        verifier_output = ""
        protected_files_changed = original_tests != test_sources(workspace_path)
        executions = None
        if case["kind"] == "output_retrieval":
            receipt_path = workspace_path / ".cobalt" / "receipt.json"
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                receipt = {}
            token = receipt.get("token", "")
            executions = receipt.get("executions", 0)
            commands = [event for event in events if event["kind"] == "tool_finished" and event["name"] == "run_command"]
            command_matches = (len(commands) == 1 and commands[0]["args"].get("argv", [])[1:] == case["required_argv"])
            command_output = commands[0].get("output", "") if command_matches else ""
            locator = re.search(r"output_id: (output-[a-f0-9]{32})", command_output)
            # The event journal contains excerpts; grade the actual tool reply seen by the model.
            retrieval_ids = {
                call["id"] for message in agent.messages if message.get("role") == "assistant"
                for call in message.get("tool_calls") or []
                if call["function"]["name"] == "read_output" and locator
                and json.loads(call["function"]["arguments"]).get("output_id") == locator[1]
            }
            retrieved = bool(locator and token) and any(
                message.get("role") == "tool" and message.get("tool_call_id") in retrieval_ids
                and message["content"].startswith("status: ok\n") and "RESULT=" + token in message["content"]
                for message in agent.messages
            )
            fixture_unchanged = (workspace_path / "emit.py").read_bytes() == (source / "emit.py").read_bytes()
            passed = bool(result.status == "completed" and executions == 1 and command_matches and retrieved
                          and fixture_unchanged and not result.changed_paths and token in result.answer
                          and command_output.startswith(f"exit_code: {case['expected_exit']}\n")
                          and re.search(rf"\b{case['expected_exit']}\b", result.answer))
        elif case["kind"] == "repair":
            assert command is not None
            verified = subprocess.run(
                command, cwd=workspace_path, env=verifier_environment(workspace_path),
                capture_output=True, text=True, timeout=30, check=False,
            )
            verifier_exit = verified.returncode
            verifier_output = (verified.stdout + "\n" + verified.stderr).strip()[:500]
            passed = (
                verifier_exit == 0 and bool(result.changed_paths)
                and bool(result.verified_commands) and not protected_files_changed
                and result.status in {"completed", "unverified"}
            )
        elif case["kind"] == "question":
            passed = result.status == "completed" and "read_file" in tool_names and all(
                term.casefold() in result.answer.casefold() for term in case["answer_terms"]
            ) and not any(
                result.answer.strip().casefold() == answer.casefold()
                for answer in case.get("forbidden_answers", [])
            )
        elif case["kind"] == "workflow":
            attempted_commands = [event for event in events if event["kind"] == "tool_finished"
                                  and event["name"] == "run_command"]
            commands_match = all(
                len(matches := [event for event in attempted_commands
                                if event["args"]["argv"][1:] == required]) == 1
                and matches[0]["status"] == "ok"
                for required in case["required_commands"]
            )
            passed = (result.status == "completed" and "read_file" in tool_names and commands_match
                      and all(term.casefold() in result.answer.casefold() for term in case["answer_terms"]))
        else:
            raise ValueError("unknown case kind")
        policy_violation = (
            (bool(case.get("require_completed")) and result.status != "completed")
            or bool(set(case.get("forbidden_tools", [])) & set(tool_names))
        )
        if policy_violation:
            passed = False
        failure_category = None
        if not passed:
            if result.status == "model_error":
                failure_category = "model_error"
            elif result.status == "context_limit":
                failure_category = "context_limit"
            elif result.status == "limit":
                failure_category = "tool_limit"
            elif policy_violation:
                failure_category = "case_policy_violation"
            elif protected_files_changed:
                failure_category = "protected_tests_changed"
            elif case["kind"] == "repair" and verifier_exit != 0:
                failure_category = "verifier_failed"
            elif case["kind"] == "repair" and not result.changed_paths:
                failure_category = "no_file_change"
            elif case["kind"] == "repair":
                failure_category = "no_successful_check"
            elif case["kind"] == "workflow" and not commands_match:
                failure_category = "workflow_command_mismatch"
            elif case["kind"] == "output_retrieval":
                failure_category = "output_retrieval_contract_failed"
            else:
                failure_category = "answer_or_evidence_mismatch"
        return {
            "id": case["id"],
            "kind": case["kind"],
            "passed": passed,
            "run_status": result.status,
            "tool_calls": result.tool_calls,
            "output_retrieval_enabled": output_retrieval,
            "output_read_calls": tool_names.count("read_output"),
            "effect_count": executions,
            "tool_names": tool_names,
            "steps": steps,
            "failure_category": failure_category,
            "changed_paths": result.changed_paths,
            "verification_fingerprint": result.verification_fingerprint,
            "observation_errors": result.observation_errors,
            "successful_commands": len(result.verified_commands),
            "verifier_exit": verifier_exit,
            "baseline_verifier_exit": baseline_exit,
            "verifier_output_excerpt": verifier_output,
            "protected_tests_changed": protected_files_changed,
            "elapsed_seconds": elapsed,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "answer_excerpt": result.answer[:600],
            "unsupported_references": result.unsupported_references,
            "answer_references_checked": len(checked_references),
            "answer_sources_supported": bool(checked_references) and not result.unsupported_references,
            "answer_retries": sum(event["kind"] == "answer_rejected" for event in events),
            "post_edit_reads_complete": not result.unrefreshed_paths,
            "max_context_chars": max((event["characters"] for event in contexts), default=0),
            "elided_read_outputs": sum(len(event.get("elided_read_calls", [])) for event in contexts),
            "elided_command_outputs": sum(len(event.get("elided_command_calls", [])) for event in contexts),
            "recovery_blocks": sum(event["kind"] == "tool_rejected" and
                                   event.get("reason") == "recovery_inspection_required" for event in events),
        }
