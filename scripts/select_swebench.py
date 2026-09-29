"""Freeze a repository-stratified SWE-bench Lite diagnostic set before inference."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def select_rows(rows: list[dict], count: int) -> list[dict]:
    repositories = sorted({row["repo"] for row in rows})
    if len(repositories) < count:
        raise ValueError(f"need {count} repositories; split has {len(repositories)}")
    selected = []
    for repo in repositories[:count]:
        candidates = (row for row in rows if row["repo"] == repo)
        selected.append(min(candidates, key=lambda row: (hashlib.sha256(row["instance_id"].encode()).hexdigest(),
                                                          row["instance_id"])))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--revision", help="Dataset commit; defaults to current HEAD and records the resolved commit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    try:
        from datasets import load_dataset
        from huggingface_hub import HfApi
    except ImportError as exc:
        parser.error(f"evaluation dependencies missing: {exc}; install swebench separately")
    revision = args.revision or HfApi().dataset_info("SWE-bench/SWE-bench_Lite").sha
    rows = list(load_dataset("SWE-bench/SWE-bench_Lite", split=args.split, revision=revision))
    selected = select_rows(rows, args.count)
    manifest = {
        "dataset": "SWE-bench/SWE-bench_Lite",
        "revision": revision,
        "split": args.split,
        "selection": "alphabetical first N repositories; minimum SHA-256(instance_id) in each",
        "selected_at": datetime.now(UTC).isoformat(),
        "tasks": [{"instance_id": row["instance_id"], "repo": row["repo"],
                   "base_commit": row["base_commit"], "problem_statement": row["problem_statement"]}
                  for row in selected],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Selected {len(selected)} tasks from {args.split} at {revision}: {args.output}")


if __name__ == "__main__":
    main()
