"""Supplement the frozen benchmark with a nonempty zero-price order boundary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cobalt.evaluation import verifier_environment

CHECK = """
import json, sys
sys.path.insert(0, sys.argv[1])
from api import quote
actual = quote([{'unit_cents': 0, 'quantity': 1}])
print(json.dumps({'actual': actual, 'passed': actual['subtotal_cents'] == 0
                 and actual['tax_cents'] == 0 and actual['shipping_cents'] == 300
                 and actual['total_cents'] == 300}))
"""


def check_zero(root):
    run = subprocess.run([sys.executable, "-I", "-B", "-c", CHECK, str(root)],
                         cwd=root, env=verifier_environment(root), capture_output=True,
                         text=True, timeout=10, check=False)
    return json.loads(run.stdout) if run.returncode == 0 else {"passed": False, "stderr": run.stderr[-1000:]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    rows = []
    for row in report["rows"]:
        with tempfile.TemporaryDirectory(prefix="cobalt-zero-audit-") as directory:
            root = Path(directory)
            for name, source in row.get("sources", {}).items():
                if Path(name).name != name or not name.endswith(".py"):
                    raise ValueError("source name is not a top-level Python file")
                (root / name).write_text(source, encoding="utf-8")
            check = check_zero(root)
            rows.append({k: row[k] for k in ("case", "policy", "attempt")} | {
                "original_task_passed": row["task_passed"], "supplement": check,
                "combined_passed": row["task_passed"] and check["passed"],
            })
    output = args.report.with_name(args.report.stem + "-zero-audit.json")
    output.write_text(json.dumps({"source_report": args.report.name,
                                 "protocol": "Additional post-run audit; original scores remain unchanged. "
                                             "A nonempty zero-price order is below the free-shipping threshold.",
                                 "rows": rows}, indent=2) + "\n", encoding="utf-8")
    print(f"Additional boundary passed: {sum(r['supplement']['passed'] for r in rows)}/{len(rows)}")
    print(f"Combined passed: {sum(r['combined_passed'] for r in rows)}/{len(rows)}")
    print(output)


if __name__ == "__main__":
    main()
