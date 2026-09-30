import hashlib
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cobalt.cli import main
from cobalt.domain import ModelTurn, ToolCall


class SequenceModel:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []

    def complete(self, messages, _schemas):
        self.seen.append(messages)
        return next(self.turns)


class CliContinuationIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("COBALT_RUN_DOCKER_TESTS") == "1", "requires a local Docker image")
    def test_structured_task_continues_in_same_isolated_copy_and_rechecks(self):
        fixture = Path(__file__).resolve().parents[1] / "benchmarks" / "fixtures" / "grades"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            shutil.copytree(fixture, source)
            spec = root / "task.json"
            spec.write_text(json.dumps({
                "request": "Return 0.0 for an empty average without changing other results.",
                "checks": [["python", "-m", "unittest", "discover", "-s", "tests", "-v"]],
                "protected_paths": ["tests/test_grades.py"],
                "require_change": True,
            }), encoding="utf-8")
            first_model = SequenceModel([
                ModelTurn("", (ToolCall("source", "read_file", {"path": "grades.py"}),)),
                ModelTurn("", (ToolCall("tests", "read_file", {"path": "tests/test_grades.py"}),)),
            ])
            output = StringIO()
            with (patch("cobalt.cli.resolve_config", return_value=object()),
                  patch("cobalt.cli.from_config", return_value=first_model),
                  redirect_stdout(output)):
                first_exit = main(["--workspace", str(source), "--execution", "container", "--yes",
                                   "--max-tool-calls", "2", "--task-file", str(spec)])
            self.assertEqual(first_exit, 1)
            self.assertIn("User-specified acceptance checks", str(first_model.seen[0][-1]["content"]))
            isolated_line = next(line for line in output.getvalue().splitlines()
                                 if line.startswith("Isolated workspace: "))
            isolated = Path(isolated_line.removeprefix("Isolated workspace: "))
            first_report = next((isolated / ".cobalt" / "runs").glob("*/report.json"))
            first_data = json.loads(first_report.read_text(encoding="utf-8"))
            self.assertEqual(first_data["agent_status"], "limit")
            self.assertTrue(first_data["require_change"])
            self.assertEqual(first_data["validation_status"], "failed")
            digest = hashlib.sha256((source / "grades.py").read_bytes()).hexdigest()
            second_model = SequenceModel([
                ModelTurn("", (ToolCall("edit", "replace_text", {
                    "path": "grades.py", "old": "    return sum(scores) / len(scores)",
                    "new": "    if not scores:\n        return 0.0\n    return sum(scores) / len(scores)",
                    "expected_sha256": digest,
                }),)),
                ModelTurn("", (ToolCall("reread", "read_file", {"path": "grades.py"}),)),
                ModelTurn("", (ToolCall("check", "run_command", {
                    "argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
                    "purpose": "check",
                }),)),
                ModelTurn("The empty average is fixed and the supplied tests pass."),
            ])
            output = StringIO()
            with (patch("cobalt.cli.resolve_config", return_value=object()),
                  patch("cobalt.cli.from_config", return_value=second_model),
                  redirect_stdout(output)):
                second_exit = main(["--workspace", str(isolated), "--execution", "container", "--yes",
                                    "--resume", first_data["session_id"], "--continue",
                                    "--max-tool-calls", "4", "--task-file", str(spec)])
            self.assertEqual(second_exit, 0, output.getvalue())
            reports = list((isolated / ".cobalt" / "runs").glob("*/report.json"))
            second_data = next(json.loads(path.read_text(encoding="utf-8")) for path in reports
                               if path.parent.name != first_data["run_id"])
            self.assertEqual(second_data["continued_from_run"], first_data["run_id"])
            self.assertEqual(second_data["agent_status"], "completed")
            self.assertTrue(second_data["require_change"])
            self.assertEqual(second_data["validation_status"], "passed")
            self.assertEqual(second_data["checks"][0]["status"], "passed")
            self.assertEqual(second_data["protected_paths_changed"], [])
            self.assertEqual((source / "grades.py").read_bytes(), (fixture / "grades.py").read_bytes())
