"""Run pinned SWE-bench issues through Cobalt; grade predictions with the official harness."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def image_name(instance_id: str) -> str:
    return f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_').lower()}:latest"


def prediction_patch(raw: str) -> tuple[str, list[str]]:
    """Submit implementation changes; retain test edits in the raw run artifact."""
    blocks = re.split(r"(?=^diff --git )", raw, flags=re.MULTILINE)
    if blocks[0]:
        raise ValueError("unexpected text before first diff header")
    kept: list[str] = []
    excluded: list[str] = []
    for block in blocks[1:]:
        header = shlex.split(block.split("\n", 1)[0])
        if len(header) != 4 or header[:2] != ["diff", "--git"] or not header[3].startswith("b/"):
            raise ValueError("unsupported Git diff header")
        name = header[3][2:]
        parts = Path(name).parts
        basename = parts[-1].lower()
        if any(part.lower() in {"tests", "test", "testing", "__tests__"} for part in parts[:-1]) or (
            basename.startswith("test_") or basename.endswith("_test.py")
        ):
            excluded.append(name)
        else:
            kept.append(block)
    return "".join(kept), excluded


def _git(root: Path, proxy: str | None, *args: str, cwd: Path | None = None) -> str:
    command = ["git"]
    if proxy:
        command.extend(["-c", f"http.proxy={proxy}"])
    command.extend(args)
    result = subprocess.run(command, cwd=cwd or root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"Git operation failed: {' '.join(args)}: {result.stderr[-600:]}")
    return result.stdout.strip()


def _read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("dataset") != "SWE-bench/SWE-bench_Lite"
            or manifest.get("split") not in {"dev", "test"}
            or not isinstance(manifest.get("revision"), str)
            or not manifest["revision"]
            or not isinstance(manifest.get("tasks"), list)):
        raise ValueError("expected a pinned SWE-bench Lite dev or test manifest")
    ids = [task["instance_id"] for task in manifest["tasks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest contains duplicate instance IDs")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instance-id", action="append", help="Run selected IDs from the manifest")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--work-root", type=Path, default=Path(tempfile.gettempdir()) / "cobalt-swebench-checkouts")
    parser.add_argument("--git-proxy", help="Optional local Git proxy; never sent to the agent container")
    parser.add_argument("--max-tool-calls", type=int, default=48)
    parser.add_argument("--allow-dirty-dev", action="store_true")
    args = parser.parse_args()
    if args.max_tool_calls < 1:
        parser.error("--max-tool-calls must be positive")
    manifest = _read_manifest(args.manifest)
    project = Path(__file__).resolve().parents[1]
    dirty = _git(project, None, "status", "--porcelain")
    if dirty and not (manifest["split"] == "dev" and args.allow_dirty_dev):
        parser.error("a test attempt requires clean, frozen agent code; use --allow-dirty-dev only for dev")
    commit = _git(project, None, "rev-parse", "HEAD")
    if not args.env_file.is_file():
        parser.error(f"local credential file is missing: {args.env_file}")
    tasks = [task for task in manifest["tasks"] if not args.instance_id or task["instance_id"] in args.instance_id]
    if len(tasks) != (len(args.instance_id) if args.instance_id else len(manifest["tasks"])):
        parser.error("requested instance ID is missing or duplicated")
    missing_images = []
    for task in tasks:
        image = image_name(task["instance_id"])
        try:
            available = subprocess.run(["docker", "image", "inspect", image],
                                       capture_output=True, timeout=10, check=False).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            available = False
        if not available:
            missing_images.append(image)
    if missing_images:
        parser.error("prepare official task images before inference: " + ", ".join(missing_images))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)
    attempts_path = args.output_dir / "attempts.jsonl"
    predictions_path = args.output_dir / "predictions.jsonl"
    existing = [json.loads(line)["instance_id"] for line in attempts_path.read_text(encoding="utf-8").splitlines()] if attempts_path.exists() else []
    if any(task["instance_id"] in existing for task in tasks):
        parser.error("selected instance already has a recorded attempt; use a new output directory")
    for task in tasks:
        instance_id = task["instance_id"]
        image = image_name(instance_id)
        checkout = args.work_root / instance_id
        if not checkout.exists():
            _git(project, args.git_proxy, "-c", "core.autocrlf=false", "clone", "--quiet", "--filter=blob:none",
                 f"https://github.com/{task['repo']}.git", str(checkout))
            _git(project, None, "-C", str(checkout), "config", "--local", "core.autocrlf", "false")
        elif _git(project, None, "-C", str(checkout), "config", "--local", "--get",
                  "core.autocrlf") != "false":
            raise RuntimeError(f"existing checkout has incompatible line-ending policy: {checkout}; use a fresh --work-root")
        elif _git(project, None, "-C", str(checkout), "status", "--porcelain"):
            raise RuntimeError(f"existing checkout has local changes: {checkout}")
        _git(project, args.git_proxy, "-C", str(checkout), "checkout", "--quiet", "--force",
             "--detach", task["base_commit"])
        if _git(project, None, "-C", str(checkout), "rev-parse", "HEAD") != task["base_commit"]:
            raise RuntimeError(f"base commit mismatch for {instance_id}")
        if _git(project, None, "-C", str(checkout), "status", "--porcelain"):
            raise RuntimeError(f"checkout is dirty before {instance_id}")
        # Reserve the attempt before model inference; interrupted test runs are never silently replayed.
        reservation = {"instance_id": instance_id, "status": "started", "source_commit": commit,
                       "dataset_revision": manifest["revision"], "image": image}
        with attempts_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(reservation, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        command = [
            sys.executable, "-m", "cobalt", "--workspace", str(checkout), "--env-file", str(args.env_file),
            "--provider", "deepseek", "--model", "deepseek-flash", "--execution", "container",
            "--image", image, "--container-env",
            "PATH=/opt/miniconda3/envs/testbed/bin:/usr/local/bin:/usr/bin:/bin",
            "--container-env", "PYTHONPATH=/workspace/src:/workspace", "--yes",
            "--max-tool-calls", str(args.max_tool_calls), task["problem_statement"],
        ]
        env = dict(os.environ)
        # The runner must import this frozen source tree, even when launched
        # from a shell with a different project's PYTHONPATH.
        env["PYTHONPATH"] = str(project / "src")
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=project, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", env=env, timeout=1800,
                                     check=False)
        except subprocess.TimeoutExpired as exc:
            def decoded(value: bytes | str | None) -> str:
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

            process = subprocess.CompletedProcess(command, 124,
                                                  decoded(exc.stdout), decoded(exc.stderr))
        (args.output_dir / f"{instance_id}.stdout.txt").write_text(process.stdout, encoding="utf-8")
        (args.output_dir / f"{instance_id}.stderr.txt").write_text(process.stderr, encoding="utf-8")
        report_line = next((line for line in process.stdout.splitlines() if line.startswith("Session: ")
                            and "; report: " in line), "")
        report_path = Path(report_line.split("; report: ", 1)[1]) if report_line else None
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path and report_path.is_file() else None
        raw_patch = (report_path.parent / "final.patch").read_bytes().decode("utf-8") if report else ""
        patch, excluded_tests = prediction_patch(raw_patch)
        if report:
            shutil.copy2(report_path, args.output_dir / f"{instance_id}.report.json")
            (args.output_dir / f"{instance_id}.raw.patch").write_bytes(raw_patch.encode("utf-8"))
            (args.output_dir / f"{instance_id}.patch").write_bytes(patch.encode("utf-8"))
        prediction = {"instance_id": instance_id, "model_name_or_path": "cobalt-deepseek-flash",
                      "model_patch": patch}
        with predictions_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(prediction, ensure_ascii=False) + "\n")
        outcome = {"instance_id": instance_id, "status": "finished" if report else "infrastructure_error",
                   "agent_status": report["agent_status"] if report else None,
                   "validation_status": report["validation_status"] if report else None,
                   "patch_bytes": len(patch.encode("utf-8")),
                   "raw_patch_bytes": len(raw_patch.encode("utf-8")),
                   "excluded_test_paths": excluded_tests,
                   "duration_seconds": round(time.monotonic() - started, 2),
                   "return_code": process.returncode,
                   "prompt_tokens": report["prompt_tokens"] if report else None,
                   "completion_tokens": report["completion_tokens"] if report else None,
                   "report_file": f"{instance_id}.report.json" if report else None}
        with attempts_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        print(f"{instance_id}: {outcome['status']} agent={outcome['agent_status']} patch={outcome['patch_bytes']}B")


if __name__ == "__main__":
    main()
