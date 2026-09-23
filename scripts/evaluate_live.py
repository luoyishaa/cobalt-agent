"""Run small, fresh-workspace DeepSeek tasks and save transparent metrics."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cobalt.evaluation import run_case
from cobalt.model import DeepSeek


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--case", action="append", help="Only run this case id; may be repeated")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "benchmarks" / "cases.json").read_text(encoding="utf-8"))
    cases = [case for case in data["cases"] if not args.case or case["id"] in args.case]
    if not cases:
        parser.error("no matching cases")
    rows = [run_case(case, root / "benchmarks", lambda: DeepSeek(model=args.model)) for case in cases]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "commit": commit.stdout.strip() if commit.returncode == 0 else "uncommitted",
        "case_count": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "pass_rate": sum(row["passed"] for row in rows) / len(rows),
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
