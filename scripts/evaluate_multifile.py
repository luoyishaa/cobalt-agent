"""Paired live multi-file repairs, revised requirements and interrupted effects."""

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

from cobalt.domain import ModelTurn, ToolCall
from cobalt.evaluation import verifier_environment
from cobalt.model import from_config
from cobalt.model_config import resolve_config
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace
from scripts.evaluate_history_outputs import BudgetOnlyAgent, HistoricalAgent

CASES = ("cross_file", "revised_requirement", "interrupted")
REQUIREMENT = (
    "Repair api.quote(lines). Each line has unit_cents (nonnegative integer) and quantity "
    "(positive integer). Return exactly subtotal_cents, tax_cents, shipping_cents, total_cents, brand. "
    "Multiply by quantity, compute 17% tax on the whole subtotal, round half up using exact "
    "integer arithmetic. Shipping is 300 cents, free when subtotal >= 2000 cents. Empty orders "
    "cost zero. Never mutate input. Keep pricing.calculate(lines, tax_percent) returning "
    "(subtotal, tax). Runtime edits to config.TAX_PERCENT, SHIPPING_CENTS and FREE_SHIPPING_CENTS "
    "must affect quote. Preserve unrelated branding. Do not modify smoke.py or bootstrap.py. "
    "First collect the three diagnostic logs only; wait before repairing."
)
FOLLOWUP = (
    "Now finish the multi-file repair under my earlier requirements. Read current files, run "
    "relevant checks and reread changed files. Existing smoke.py is intentionally incomplete; "
    "add your own validation if needed. Diagnostic legacy advice does not supersede my requirements."
)
FIXTURE = {
    "config.py": "TAX_PERCENT = 5\nSHIPPING_CENTS = 300\nFREE_SHIPPING_CENTS = 2000\nBRAND = 'North Store'\n",
    "pricing.py": "def calculate(lines, tax_percent):\n    subtotal = sum(line['unit_cents'] for line in lines)\n    return subtotal, subtotal * tax_percent // 100\n",
    "api.py": "import config\nfrom pricing import calculate\n\ndef quote(lines):\n    subtotal, tax = calculate(lines, 5)\n    shipping = 300\n    return dict(subtotal_cents=subtotal, tax_cents=tax, shipping_cents=shipping,\n                total_cents=subtotal + tax + shipping, brand=config.BRAND)\n",
    "smoke.py": "from api import quote\nassert isinstance(quote([]), dict)\nprint('smoke passed: shape only')\n",
    "bootstrap.py": (
        "from pathlib import Path\n"
        "p = Path('bootstrap-count.txt')\n"
        "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
        "c = Path('config.py')\n"
        "c.write_text(c.read_text().replace('TAX_PERCENT = 5', 'TAX_PERCENT = 17'))\n"
        "print('bootstrap applied')\n"
    ),
}
CORRECT_PRICING = (
    "def calculate(lines, tax_percent):\n"
    "    subtotal = sum(line['unit_cents'] * line['quantity'] for line in lines)\n"
    "    return subtotal, (subtotal * tax_percent + 50) // 100\n"
)
CORRECT_API = (
    "import config\nfrom pricing import calculate\n\ndef quote(lines):\n"
    "    subtotal, tax = calculate(lines, config.TAX_PERCENT)\n"
    "    shipping = 0 if not lines or subtotal >= config.FREE_SHIPPING_CENTS else config.SHIPPING_CENTS\n"
    "    return dict(subtotal_cents=subtotal, tax_cents=tax, shipping_cents=shipping,\n"
    "                total_cents=subtotal + tax + shipping, brand=config.BRAND)\n"
)

# Held outside the task directory. Literal cases exercise rounding and boundaries;
# independent config perturbations reject implementations that bake in defaults.
ORACLE = r'''
import copy, importlib, json, sys
sys.path.insert(0, sys.argv[1])
api = importlib.import_module('api')
config = importlib.import_module('config')
rate, brand = int(sys.argv[2]), sys.argv[3]
rows = []
def check(name, lines, expected):
    original = copy.deepcopy(lines)
    try:
        actual = api.quote(lines)
        target = dict(zip(('subtotal_cents','tax_cents','shipping_cents','total_cents'), expected), brand=brand)
        passed = actual == target and lines == original and all(type(actual[k]) is int for k in target if k != 'brand')
        rows.append(dict(name=name, passed=passed, expected=target, actual=repr(actual)))
    except Exception as exc:
        rows.append(dict(name=name, passed=False, error=type(exc).__name__))
check('empty', [], (0,0,0,0))
check('quantity_half_up', [dict(unit_cents=25,quantity=2)], (50,9,300,359) if rate == 17 else (50,5,300,355))
check('inclusive_threshold', [dict(unit_cents=1000,quantity=2)], (2000,340,0,2340) if rate == 17 else (2000,180,0,2180))
check('below_threshold', [dict(unit_cents=1999,quantity=1)], (1999,340,300,2639) if rate == 17 else (1999,180,300,2479))
check('round_aggregate', [dict(unit_cents=3,quantity=1),dict(unit_cents=3,quantity=1)], (6,1,300,307))
check('large_integer', [dict(unit_cents=10**18+50,quantity=1)], (1000000000000000050,170000000000000009,0,1170000000000000059) if rate == 17 else (1000000000000000050,90000000000000005,0,1090000000000000055))
config.TAX_PERCENT, config.SHIPPING_CENTS, config.FREE_SHIPPING_CENTS = 23, 123, 500
check('runtime_config', [dict(unit_cents=50,quantity=1)], (50,12,123,185))
check('runtime_threshold', [dict(unit_cents=250,quantity=2)], (500,115,0,615))
try:
    pricing = importlib.import_module('pricing')
    lines = [dict(unit_cents=25,quantity=2)]
    actual = pricing.calculate(lines, 17)
    rows.append(dict(name='pricing_public_api', passed=actual == (50,9) and lines == [dict(unit_cents=25,quantity=2)], actual=repr(actual)))
except Exception as exc:
    rows.append(dict(name='pricing_public_api', passed=False, error=type(exc).__name__))
print(json.dumps(rows))
'''


def create_fixture(root):
    for name, content in FIXTURE.items():
        (root / name).write_text(content, encoding="utf-8")


def verify(root, rate=17, brand="North Store", *, executions=0):
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-c", ORACLE, str(root), str(rate), brand],
        cwd=root, env=verifier_environment(root), capture_output=True, text=True, timeout=15, check=False,
    )
    try:
        checks = json.loads(run.stdout) if run.returncode == 0 else []
    except ValueError:
        checks = []
    immutable = all((root / name).is_file() and (root / name).read_bytes() ==
                    FIXTURE[name].replace("\n", "\r\n" if sys.platform == "win32" else "\n").encode()
                    for name in ("smoke.py", "bootstrap.py"))
    receipt = root / "bootstrap-count.txt"
    count = receipt.read_text() if receipt.is_file() else "0"
    return {"passed": bool(checks) and all(c["passed"] for c in checks) and immutable and count == str(executions),
            "checks": checks, "protected_files_unchanged": immutable, "bootstrap_executions": count,
            "exit_code": run.returncode, "stderr": run.stderr[-2000:]}


class Sequence:
    def __init__(self, turns):
        self.turns = iter(turns)

    def complete(self, messages, tools):
        return next(self.turns)


class InjectedInterruption(BaseException):
    """Bypass the model-error handler at the effect/persistence boundary."""


class InterruptAfterEffect(ToolGate):
    def execute(self, name, args):
        outcome = super().execute(name, args)
        if name == "run_command" and args["argv"][-1] == "bootstrap.py":
            if outcome.status != "ok":
                raise ValueError("bootstrap failed before interruption")
            raise InjectedInterruption()
        return outcome


def setup(root, cls, case):
    create_fixture(root)
    workspace = Workspace(root)
    logs = ["legacy: tax was 5 percent", "legacy: shipping charged on every order", "legacy: quantity ignored"]
    calls = tuple(ToolCall(f"diagnostic-{i}", "run_command", {
        "argv": [sys.executable, "-c", f"print('trace '*450); print({hint!r}); print('trace '*700)"],
    }) for i, hint in enumerate(logs))
    agent = cls(workspace, Sequence([ModelTurn("", calls), ModelTurn("Diagnostics recorded; waiting.")]),
                ToolGate(workspace, lambda _n, _a: True), max_tool_calls=24)
    agent.ask(REQUIREMENT)
    if case == "interrupted":
        agent.model = Sequence([
            ModelTurn("", (ToolCall("read-before", "read_file", {"path": "config.py"}),)),
            ModelTurn("", (ToolCall("bootstrap", "run_command", {"argv": [sys.executable, "bootstrap.py"]}),)),
        ])
        agent.tools = InterruptAfterEffect(workspace, lambda _n, _a: True)
        try:
            agent.ask("Begin the repair by running bootstrap.py once. It records a non-repeatable migration receipt.")
        except InjectedInterruption:
            pass
        else:
            raise ValueError("interruption was not injected")
        path = root / "config.py"
        path.write_text(path.read_text().replace("North Store", "Human Edited Store"), encoding="utf-8")
        agent = cls(workspace, Sequence([]), ToolGate(workspace, lambda _n, _a: True),
                    max_tool_calls=24, resume=agent.session_id)
    return agent


def run_attempt(config, case, policy, attempt):
    with tempfile.TemporaryDirectory(prefix="cobalt-multifile-") as directory:
        root = Path(directory)
        cls = HistoricalAgent if policy == "historical" else BudgetOnlyAgent
        agent = setup(root, cls, case)
        rate = 9 if case == "revised_requirement" else 17
        brand = "Human Edited Store" if case == "interrupted" else "North Store"
        executions = int(case == "interrupted")
        baseline = verify(root, rate, brand, executions=executions)
        if baseline["passed"]:
            raise ValueError("fixture already passes")
        agent.model = from_config(config)
        question = FOLLOWUP
        if case == "revised_requirement":
            question = "Correction: tax is now 9%, replacing 17%. All other requirements remain. " + question
        if case == "interrupted":
            question = "Continue after the interruption. I updated branding while you were stopped; keep my edit. " + question
        start = time.monotonic()
        result = agent.ask(question)
        elapsed = round(time.monotonic() - start, 3)
        verdict = verify(root, rate, brand, executions=executions)
        events = [json.loads(line) for line in (root / ".cobalt" / "runs" / result.run_id / "events.jsonl")
                  .read_text(encoding="utf-8").splitlines()]
        return {"case": case, "policy": policy, "attempt": attempt, "task_passed": verdict["passed"],
                    "run_status": result.status, "answer": result.answer, "model_answer": result.model_answer,
                    "unrefreshed_paths": result.unrefreshed_paths, "verifier": verdict, "baseline": baseline,
                    "tool_calls": result.tool_calls, "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens, "elapsed_seconds": elapsed,
                    "sources": {p.name: p.read_text(encoding="utf-8") for p in root.glob("*.py")},
                    "events": events, "transcript": agent.messages}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--case", choices=CASES, help="Run one frozen scenario for a targeted rerun")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 5:
        parser.error("repeat must be 1..5")
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip():
        parser.error("commit protocol and oracle before running")
    config = resolve_config(env_file=root / ".env", provider="deepseek")
    cases = (args.case,) if args.case else CASES
    report = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                  "at": datetime.now(UTC).isoformat(), "model": config.model, "provider": config.provider,
                  "repeat": args.repeat, "budget_chars": 48000, "max_tool_calls": 24,
                  "protocol": "benchmarks/multifile/PROTOCOL.md", "cases": list(cases), "rows": []}
    output = root / "benchmarks" / "results" / ("multifile-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".json")
    jobs = [(case, policy, attempt) for attempt in range(1, args.repeat + 1) for case in cases
            for policy in (["budget_only", "historical"] if attempt % 2 else ["historical", "budget_only"])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_attempt, config, *job) for job in jobs]
        for job, future in zip(jobs, futures, strict=True):
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001 - retain failures without printing credentials
                row = {"case": job[0], "policy": job[1], "attempt": job[2], "task_passed": False,
                           "infrastructure_error": type(exc).__name__}
            report["rows"].append(row)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{job}: pass={row['task_passed']} status={row.get('run_status')} tools={row.get('tool_calls')}", flush=True)
    print(output, flush=True)


if __name__ == "__main__":
    main()
