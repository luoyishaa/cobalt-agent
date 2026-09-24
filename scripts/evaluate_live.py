"""Run fresh-workspace coding tasks and save transparent per-attempt metrics."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cobalt.evaluation import run_case
from cobalt.model import from_config
from cobalt.model_config import resolve_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", help="Override the local provider selection")
    parser.add_argument("--model", help="Override the selected provider model")
    parser.add_argument("--repeat", type=int, default=1, help="Independent attempts per case (1..10)")
    parser.add_argument("--case", action="append", help="Only run this case id; may be repeated")
    parser.add_argument("--allow-dirty", action="store_true", help="Run an exploratory check with uncommitted code")
    parser.add_argument("--without-output-retrieval", action="store_true", help="Ablate the saved-output read tool")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be 1..10")
    root = Path(__file__).resolve().parents[1]
    config = resolve_config(env_file=root / ".env", provider=args.provider, model=args.model)
    data = json.loads((root / "benchmarks" / "cases.json").read_text(encoding="utf-8"))
    cases = [case for case in data["cases"] if not args.case or case["id"] in args.case]
    if not cases:
        parser.error("no matching cases")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if dirty.returncode != 0:
        parser.error("cannot read Git working tree status")
    if dirty.stdout.strip() and not args.allow_dirty:
        parser.error("commit changes before a reproducible run, or use --allow-dirty for exploration")
    rows = []
    for case in cases:
        for attempt in range(1, args.repeat + 1):
            row = run_case(case, root / "benchmarks", lambda: from_config(config),
                           output_retrieval=not args.without_output_retrieval)
            row["attempt"] = attempt
            rows.append(row)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "provider": config.provider,
        "model": config.model,
        "output_retrieval_enabled": not args.without_output_retrieval,
        "repeats_per_case": args.repeat,
        "commit": commit.stdout.strip() if commit.returncode == 0 else "uncommitted",
        "working_tree_dirty": bool(dirty.stdout.strip()),
        "case_count": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "pass_rate": sum(row["passed"] for row in rows) / len(rows),
        "answer_sources_supported": sum(row["answer_sources_supported"] for row in rows),
        "answer_references_checked": sum(row["answer_references_checked"] for row in rows),
        "total_prompt_tokens": sum(row["prompt_tokens"] for row in rows),
        "total_completion_tokens": sum(row["completion_tokens"] for row in rows),
        "failure_categories": {
            category: sum(row["failure_category"] == category for row in rows)
            for category in sorted({row["failure_category"] for row in rows if row["failure_category"]})
        },
        "rows": rows,
    }
    output = root / "benchmarks" / "results"
    output.mkdir(exist_ok=True)
    path = output / ("live-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['passed']}/{report['case_count']} tasks passed; report: {path}")
    for row in rows:
        print(f"{row['id']}: passed={row['passed']} status={row['run_status']} tools={row['tool_calls']} seconds={row['elapsed_seconds']}")


if __name__ == "__main__":
    main()
