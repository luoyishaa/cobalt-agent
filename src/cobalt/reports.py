"""Human-readable run evidence with a separately labeled task verdict."""

from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .domain import RunResult
from .workspace import SKIP_DIRS, private_name


def _safe_name(name: str) -> bool:
    return not any(part in SKIP_DIRS or private_name(part) for part in Path(name).parts)


def final_patch(root: Path) -> tuple[str, list[str]]:
    """Return the final Git diff against HEAD; report omitted additions explicitly."""
    try:
        tracked = subprocess.run(["git", "diff", "--name-only", "-z", "HEAD"], cwd=root,
                                 capture_output=True, timeout=15, check=True).stdout
        added = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                               cwd=root, capture_output=True, timeout=15, check=True).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "", ["Git baseline unavailable; final patch could not be generated"]
    changed_names = [name for name in tracked.decode("utf-8", errors="replace").split("\0") if name]
    safe_names = [name for name in changed_names if _safe_name(name)]
    warnings = [f"Excluded private or runtime path: {name}" for name in changed_names if not _safe_name(name)]
    patch = ""
    if safe_names:
        try:
            raw = subprocess.run(["git", "diff", "--binary", "HEAD", "--", *safe_names], cwd=root,
                                 capture_output=True, timeout=30, check=True).stdout
            patch = raw.decode("utf-8", errors="replace")
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            warnings.append("Tracked diff could not be generated")
    for name in [item for item in added.decode("utf-8", errors="replace").split("\0") if item]:
        if not _safe_name(name):
            continue
        path = root / name
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
                warnings.append(f"Untracked addition omitted from patch: {name}")
                continue
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (OSError, UnicodeError):
            warnings.append(f"Untracked addition omitted from patch: {name}")
            continue
        patch += (f"diff --git a/{name} b/{name}\nnew file mode 100644\n"
                  + "".join(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{name}")))
    return patch, warnings


def exploration_metrics(events: list[dict[str, Any]]) -> dict[str, int | None]:
    """Measure navigation without treating repeat reads as necessarily wasteful."""
    calls = 0
    first_change_call = None
    source_read_calls = 0
    fully_repeated_source_reads = 0
    repeated_discovery_calls = 0
    seen_lines: dict[tuple[str, str], set[int]] = {}
    seen_discovery: set[tuple[str, str]] = set()
    for event in events:
        if event.get("kind") != "tool_finished":
            continue
        calls += 1
        name = event.get("name")
        if name == "read_file" and event.get("status") == "ok" and event.get("path") and event.get("digest"):
            source_read_calls += 1
            args = event.get("args", {})
            start, lines = args.get("start", 1), args.get("lines", 160)
            if isinstance(start, int) and isinstance(lines, int) and start > 0 and lines > 0:
                requested = set(range(start, start + lines))
                covered = seen_lines.setdefault((event["path"], event["digest"]), set())
                if requested <= covered:
                    fully_repeated_source_reads += 1
                covered.update(requested)
        elif name in {"search", "list_files"} and event.get("status") == "ok":
            key = (name, json.dumps(event.get("args", {}), sort_keys=True, ensure_ascii=False))
            if key in seen_discovery:
                repeated_discovery_calls += 1
            seen_discovery.add(key)
        if event.get("changes"):
            if first_change_call is None:
                first_change_call = calls
            seen_discovery.clear()
    return {
        "calls_before_first_change": first_change_call - 1 if first_change_call is not None else calls,
        "first_change_call": first_change_call,
        "source_read_calls": source_read_calls,
        "fully_repeated_source_reads": fully_repeated_source_reads,
        "repeated_discovery_calls": repeated_discovery_calls,
    }


def initial_report(root: Path, result: RunResult, directory: Path) -> dict[str, Any]:
    patch, warnings = final_patch(root)
    (directory / "final.patch").write_bytes(patch.encode("utf-8"))
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    started = next((event for event in events if event["kind"] == "run_started"), {})
    continuation = next((event for event in events if event["kind"] == "run_continued"), None)
    tool_counts: dict[str, int] = {}
    last_modified_index = max((index for index, event in enumerate(events)
                               if event["kind"] == "tool_finished" and event.get("changes")), default=-1)
    check_evidence = []
    for event in events:
        if event["kind"] == "tool_finished":
            name = event["name"]
            tool_counts[name] = tool_counts.get(name, 0) + 1
    for index, event in enumerate(events):
        if (index > last_modified_index and event["kind"] == "tool_finished"
                and event.get("name") == "run_command" and event.get("status") == "ok"
                and event.get("args", {}).get("purpose") == "check"
                and event.get("args", {}).get("argv") in result.verified_commands
                and event.get("workspace_fingerprint") == result.verification_fingerprint):
            check_evidence.append({"argv": event["args"]["argv"], "status": "passed",
                                   "at": event["at"], "output_excerpt": event.get("output", "")[:2000],
                                   "workspace_fingerprint": event["workspace_fingerprint"]})
    report = {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "continued_from_run": continuation["previous_run_id"] if continuation else None,
        "inherited_changed_paths": continuation["inherited_changed_paths"] if continuation else [],
        "agent_status": result.status,
        "require_change": started.get("require_change", False),
        "validation_status": "self_checked" if result.verified_commands else "not_checked",
        "validation_source": "agent_selected" if result.verified_commands else "none",
        "verified_commands": result.verified_commands,
        "checks_after_last_change": check_evidence,
        "last_change_at": events[last_modified_index]["at"] if last_modified_index >= 0 else None,
        "tool_counts": tool_counts,
        "exploration": exploration_metrics(events),
        "changed_paths": result.changed_paths,
        "post_edit_reads_complete": not result.unrefreshed_paths,
        "observation_errors": result.observation_errors,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "patch_warnings": warnings,
        "patch_bytes": len(patch.encode("utf-8")),
        "result": asdict(result),
    }
    save_report(directory, report)
    return report


def save_report(directory: Path, report: dict[str, Any]) -> None:
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
