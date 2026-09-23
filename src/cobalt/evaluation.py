"""Fresh-workspace evaluation with separate task and runtime outcomes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

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


def run_case(case: dict[str, Any], fixtures_root: Path, model_factory: Callable[[], Model]) -> dict[str, Any]:
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
        agent = Agent(workspace, model_factory(), ToolGate(workspace, lambda _name, _args: True), max_tool_calls=12)
        started = time.monotonic()
        result = agent.ask(case["request"])
        elapsed = round(time.monotonic() - started, 3)
        event_path = workspace_path / ".cobalt" / "runs" / result.run_id / "events.jsonl"
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        tool_names = [event["name"] for event in events if event["kind"] == "tool_finished"]
        audits = [event for event in events if event["kind"] == "answer_audited"]
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
        if case["kind"] == "repair":
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
        else:
            raise ValueError("unknown case kind")
        failure_category = None
        if not passed:
            if result.status == "model_error":
                failure_category = "model_error"
            elif result.status == "limit":
                failure_category = "tool_limit"
            elif protected_files_changed:
                failure_category = "protected_tests_changed"
            elif case["kind"] == "repair" and verifier_exit != 0:
                failure_category = "verifier_failed"
            elif case["kind"] == "repair" and not result.changed_paths:
                failure_category = "no_file_change"
            elif case["kind"] == "repair":
                failure_category = "no_successful_check"
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
        }
