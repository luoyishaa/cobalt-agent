"""Human-facing command line for repository conversations."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
from pathlib import Path

from .engine import Agent
from .execution import DockerCommandRunner
from .isolation import is_isolated_workspace, prepare_isolated_workspace
from .model import from_config
from .model_config import resolve_config
from .reports import save_report
from .session import SessionStore
from .task import capture_protected, evaluate_task, load_task
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
    parser.add_argument("--continue", dest="continue_task", action="store_true",
                        help="Continue the unfinished task in a resumed session with a fresh check requirement")
    parser.add_argument("--execution", choices=("local", "container"), default="local",
                        help="Commands run locally with approval, or in an isolated Docker task copy")
    parser.add_argument("--image", default="cobalt/python:3.11", help="Existing image for container execution")
    parser.add_argument("--container-env", action="append", default=[], metavar="NAME=VALUE",
                        help="Explicit non-secret variable inside the command container; may be repeated")
    parser.add_argument("--yes", action="store_true", help="Approve actions automatically; requires container execution")
    parser.add_argument("--task-file", type=Path, help="JSON task with request, checks, and protected_paths")
    parser.add_argument("--report", metavar="RUN_ID", help="Show a saved run report without contacting a model")
    parser.add_argument("--doctor", action="store_true", help="Check local installation and container availability")
    args = parser.parse_args(argv)
    if args.max_tool_calls < 1:
        parser.error("--max-tool-calls must be positive")
    if args.yes and args.execution != "container":
        parser.error("--yes requires --execution container; local commands need individual approval")
    if args.task_file and args.question:
        parser.error("provide a question or --task-file, not both")
    if args.continue_task and (not args.resume or args.question):
        parser.error("--continue requires --resume and no new question")
    container_env = {}
    for entry in args.container_env:
        name, separator, value = entry.partition("=")
        if not separator:
            parser.error("--container-env needs NAME=VALUE")
        container_env[name] = value
    if args.doctor:
        print(f"Python: {sys.version.split()[0]}")
        print(f"Git: {shutil.which('git') or 'unavailable'}")
        try:
            DockerCommandRunner(args.workspace, args.image, container_env=container_env)
        except (ValueError, OSError) as exc:
            print(f"Container: {exc}")
            return 2
        else:
            print(f"Container: ready ({args.image})")
        return 0
    if args.report:
        if not re.fullmatch(r"run-[a-f0-9]{12}", args.report):
            parser.error("--report needs a run ID such as run-012345abcdef")
        try:
            saved = json.loads((args.workspace / ".cobalt" / "runs" / args.report / "report.json")
                               .read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"Report unavailable: {exc}", file=sys.stderr)
            return 2
        if not isinstance(saved, dict) or not {"agent_status", "validation_status", "validation_source",
                                               "tool_counts", "patch_bytes", "patch_warnings"} <= set(saved):
            print("Report unavailable: incomplete report.json", file=sys.stderr)
            return 2
        print(f"Execution: {saved['agent_status']}")
        print(f"Validation: {saved['validation_status']} ({saved['validation_source']})")
        print(f"Tool calls: {saved['tool_counts']}")
        if saved.get("continued_from_run"):
            print(f"Continued from: {saved['continued_from_run']}")
        if saved.get("exploration"):
            print(f"Exploration: {saved['exploration']}")
        print(f"Patch bytes: {saved['patch_bytes']}; warnings: {saved['patch_warnings']}")
        print(f"Agent-selected checks: {saved.get('verified_commands', [])}")
        print(f"Checks after last change: {[(check['argv'], check['status']) for check in saved.get('checks_after_last_change', [])]}")
        for check in saved.get("checks", []):
            print(f"User check: {check['status']} {check['argv']}")
        if saved.get("protected_paths_changed"):
            print(f"Protected paths changed: {saved['protected_paths_changed']}")
        print(f"Final patch: {args.workspace / '.cobalt' / 'runs' / args.report / 'final.patch'}")
        return 0
    try:
        task = load_task(args.task_file) if args.task_file else None
        source = Workspace(args.workspace)
        if args.execution == "container":
            # Check the runtime before copying large repositories or resolving credentials.
            DockerCommandRunner(source.root, args.image, container_env=container_env)
            if args.resume:
                if not is_isolated_workspace(source.root):
                    raise ValueError("resume in container mode requires the isolated workspace printed by the earlier run")
                isolated = source.root
            else:
                isolated = prepare_isolated_workspace(source.root)
            workspace = Workspace(isolated, command_runner=DockerCommandRunner(
                isolated, args.image, container_env=container_env))
            print(f"Isolated workspace: {isolated}")
        else:
            workspace = source
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
        if args.continue_task:
            unfinished = agent.unfinished_task
            if unfinished is None:
                raise ValueError("the resumed session has no unfinished task")
            prior_path = workspace.root / ".cobalt" / "runs" / unfinished.last_run_id / "report.json"
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            if not isinstance(prior, dict) or "execution_mode" not in prior:
                raise ValueError("the previous CLI report is incomplete; inspect the workspace before continuing")
            prior_contract = prior.get("task_contract_fingerprint")
            if task is None and prior_contract:
                raise ValueError("continuing this task requires its original --task-file")
            if task is not None:
                if task.fingerprint() != prior_contract:
                    raise ValueError("--task-file differs from the unfinished task's acceptance contract")
                protected_before = prior.get("protected_baseline")
                if not isinstance(protected_before, dict) or set(protected_before) != set(task.protected_paths):
                    raise ValueError("the original protected-file baseline is unavailable")
                if any(check.get("changes_during_check") for check in prior.get("checks", [])):
                    raise ValueError("a prior acceptance check changed files; reconcile them before continuing")
            else:
                protected_before = {}
        else:
            protected_before = capture_protected(workspace, task) if task else {}
    except (ValueError, OSError) as exc:
        print(f"Session error: {exc}", file=sys.stderr)
        return 2

    approval = _approval(args.yes)

    def ask(question: str) -> int:
        result = agent.continue_task() if args.continue_task else agent.ask(question)
        report_path = workspace.root / ".cobalt" / "runs" / result.run_id
        report = json.loads((report_path / "report.json").read_text(encoding="utf-8"))
        report["execution_mode"] = args.execution
        if args.execution == "container":
            report["container_image"] = args.image
            report["container_image_id"] = workspace.command_runner.image_id.decode("ascii").strip()
            report["container_env_names"] = sorted(container_env)
        if task:
            report.update(evaluate_task(workspace, result, task, protected_before, approval))
            report["task_contract_fingerprint"] = task.fingerprint()
            report["protected_baseline"] = protected_before
        save_report(report_path, report)
        print(result.answer)
        print(f"\nExecution: {result.status}; validation: {report['validation_status']} "
              f"({report['validation_source']})")
        print(f"Session: {result.session_id}; report: {report_path / 'report.json'}")
        return 0 if result.status == "completed" and (not task or report["validation_status"] == "passed") else 1

    if args.question or task or args.continue_task:
        return ask(task.agent_request() if task else " ".join(args.question))
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
