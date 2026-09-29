"""Check the frozen ten-issue release gate against an official SWE-bench report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def summarize(manifest: dict, attempts: list[dict], predictions: list[dict], official: dict,
              report_dir: Path) -> dict:
    tasks = manifest["tasks"]
    ids = [task["instance_id"] for task in tasks]
    expected = set(ids)
    if len(ids) != 10 or len(expected) != 10 or len({task["repo"] for task in tasks}) != 10:
        raise ValueError("release gate requires ten distinct repositories and instances")
    started = [record["instance_id"] for record in attempts if record["status"] == "started"]
    finished = [record["instance_id"] for record in attempts if record["status"] != "started"]
    if sorted(started) != sorted(ids) or sorted(finished) != sorted(ids):
        raise ValueError("every selected issue needs one started and one final attempt record")
    if sorted(prediction["instance_id"] for prediction in predictions) != sorted(ids):
        raise ValueError("predictions must cover exactly the selected issues once")
    if set(official.get("submitted_ids", [])) != expected:
        raise ValueError("official report does not cover the selected issues")
    resolved = set(official.get("resolved_ids", []))
    if not resolved <= expected:
        raise ValueError("official report contains unexpected resolved instances")
    violations = []
    for record in attempts:
        if record["status"] == "started":
            continue
        report_name = record.get("report_file")
        if report_name is None:
            violations.append(record["instance_id"] + ": no local isolation report")
            continue
        if Path(report_name).name != report_name:
            violations.append(record["instance_id"] + ": invalid report path")
            continue
        report_path = report_dir / report_name
        if not report_path.is_file():
            violations.append(record["instance_id"] + ": missing local report")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("execution_mode") != "container" or not report.get("container_image_id"):
            violations.append(record["instance_id"] + ": isolation metadata missing")
    repositories = {task["repo"] for task in tasks if task["instance_id"] in resolved}
    result = {
        "selected": len(ids),
        "attempted": len(finished),
        "official_resolved": len(resolved),
        "resolved_ids": sorted(resolved),
        "resolved_repositories": sorted(repositories),
        "official_infrastructure_errors": sorted(official.get("infra_failure_ids", [])),
        "local_infrastructure_errors": sorted(record["instance_id"] for record in attempts
                                               if record["status"] == "infrastructure_error"),
        "isolation_violations": violations,
    }
    result["release_gate_met"] = len(resolved) >= 3 and len(repositories) >= 2 and not violations
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--official-summary", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        _jsonl(args.attempt_dir / "attempts.jsonl"),
        _jsonl(args.attempt_dir / "predictions.jsonl"),
        json.loads(args.official_summary.read_text(encoding="utf-8")),
        args.attempt_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["release_gate_met"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
