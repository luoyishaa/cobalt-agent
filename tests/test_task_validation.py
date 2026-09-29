import json
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import RunResult
from cobalt.task import capture_protected, evaluate_task, load_task
from cobalt.workspace import Workspace


class TaskValidationTests(unittest.TestCase):
    def test_user_check_passes_but_protected_file_change_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            protected = root / "tests" / "test_app.py"
            protected.write_text("assert True\n", encoding="utf-8")
            spec_path = root / "task.json"
            spec_path.write_text(json.dumps({
                "request": "Repair app behavior",
                "checks": [[sys.executable, "-c", "print('PASS')"]],
                "protected_paths": ["tests/test_app.py"],
            }), encoding="utf-8")
            task = load_task(spec_path)
            agent_request = task.agent_request()
            self.assertIn("Repair app behavior", agent_request)
            self.assertIn(json.dumps([sys.executable, "-c", "print('PASS')"]), agent_request)
            self.assertIn("tests/test_app.py", agent_request)
            workspace = Workspace(root)
            baseline = capture_protected(workspace, task)
            protected.write_text("assert False\n", encoding="utf-8")
            report = evaluate_task(workspace, RunResult("run-1", "done", "completed", 1),
                                   task, baseline, lambda _name, _args: True)
            self.assertEqual(report["validation_status"], "failed")
            self.assertEqual(report["checks"][0]["status"], "passed")
            self.assertEqual(report["protected_paths_changed"], ["tests/test_app.py"])

    def test_agent_selected_check_is_not_labeled_user_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            report = evaluate_task(
                Workspace(Path(directory)),
                RunResult("run-2", "done", "completed", 1,
                          verified_commands=[["python", "-m", "pytest"]]),
                None, {}, lambda _name, _args: True,
            )
            self.assertEqual(report["validation_status"], "self_checked")
            self.assertEqual(report["validation_source"], "agent_selected")
