"""Explicit task checks, kept separate from the agent's own completion status."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import RunResult
from .workspace import SKIP_DIRS, Workspace, private_name


@dataclass(frozen=True)
class TaskSpec:
    request: str
    checks: tuple[tuple[str, ...], ...]
    protected_paths: tuple[str, ...]


def load_task(path: Path) -> TaskSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read task file: {path}") from exc
    if not isinstance(raw, dict) or set(raw) != {"request", "checks", "protected_paths"}:
        raise ValueError("task file requires exactly request, checks, and protected_paths")
    request, checks, protected = raw["request"], raw["checks"], raw["protected_paths"]
    if not isinstance(request, str) or not request.strip():
        raise ValueError("task request must be a non-empty string")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv)
        for argv in checks
    ):
        raise ValueError("task checks must be non-empty argv arrays")
    if not isinstance(protected, list) or any(not isinstance(item, str) or not item for item in protected):
        raise ValueError("protected_paths must be a list of relative file paths")
    return TaskSpec(request, tuple(tuple(argv) for argv in checks), tuple(protected))


def _protected_path(workspace: Workspace, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or not name or ".." in relative.parts or any(
        part in SKIP_DIRS or private_name(part) for part in relative.parts
    ):
        raise ValueError(f"invalid protected path: {name}")
    path = (workspace.root / relative).resolve(strict=False)
    if not path.is_relative_to(workspace.root) or path.is_symlink():
        raise ValueError(f"protected path escapes workspace: {name}")
    return path


def capture_protected(workspace: Workspace, task: TaskSpec) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in task.protected_paths:
        path = _protected_path(workspace, name)
        if path.exists() and not path.is_file():
            raise ValueError(f"protected path is not a file: {name}")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def evaluate_task(
    workspace: Workspace, result: RunResult, task: TaskSpec | None,
    protected_before: dict[str, str | None], approve: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "agent_status": result.status,
        "validation_source": "user_specified" if task else "agent_selected" if result.verified_commands else "none",
        "validation_status": "not_checked",
        "checks": [],
        "protected_paths_changed": [],
        "agent_selected_checks": result.verified_commands,
    }
    if task is None:
        report["validation_status"] = "self_checked" if result.verified_commands else "not_checked"
        return report
    for argv in task.checks:
        if not approve("run_command", {"argv": list(argv)}):
            report["checks"].append({"argv": list(argv), "status": "denied", "output": ""})
            continue
        before = workspace.snapshot()
        outcome = workspace.run_command(list(argv))
        after = workspace.snapshot()
        changes = after.changes_from(before)
        report["checks"].append({
            "argv": list(argv),
            "status": "passed" if outcome.status == "ok" and not changes and not after.errors else "failed",
            "output": outcome.message[:2000],
            "changes_during_check": changes,
            "observation_errors": list(after.errors),
        })
    protected_after = capture_protected(workspace, task)
    report["protected_paths_changed"] = [name for name, digest in protected_before.items()
                                          if protected_after[name] != digest]
    report["validation_status"] = (
        "passed" if all(check["status"] == "passed" for check in report["checks"])
        and not report["protected_paths_changed"] else "failed"
    )
    return report
