import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import RunResult
from cobalt.journal import Journal
from cobalt.reports import final_patch


class RunReportTests(unittest.TestCase):
    def test_report_retains_last_edit_check_result(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory), "run-evidence")
            journal.add("tool_finished", name="replace_text", args={"path": "app.py"},
                        status="ok", changes={"app.py": "modified"})
            journal.add("tool_finished", name="run_command", args={"argv": ["python", "-m", "pytest"],
                                                                   "purpose": "inspect"},
                        status="ok", changes={}, output="exit_code: 0\ninspected",
                        workspace_fingerprint="current")
            journal.add("tool_finished", name="run_command", args={"argv": ["python", "-m", "pytest"],
                                                                   "purpose": "check"},
                        status="ok", changes={}, output="exit_code: 0\n2 passed",
                        workspace_fingerprint="current")
            journal.finish(RunResult("run-evidence", "done", "completed", 2,
                                     verified_commands=[["python", "-m", "pytest"]],
                                     verification_fingerprint="current"))
            report = json.loads((journal.directory / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["checks_after_last_change"][0]["argv"],
                             ["python", "-m", "pytest"])
            self.assertEqual(len(report["checks_after_last_change"]), 1)
            self.assertIn("2 passed", report["checks_after_last_change"][0]["output_excerpt"])
            self.assertIsNotNone(report["last_change_at"])

    def test_new_file_patch_applies_to_a_clean_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost",
                            "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
            (root / "new.py").write_text("ANSWER = 42\n", encoding="utf-8")
            patch, warnings = final_patch(root)
            self.assertEqual(warnings, [])
            self.assertIn("diff --git a/new.py b/new.py", patch)
            (root / "new.py").unlink()
            result = subprocess.run(["git", "apply", "--check", "-"], cwd=root,
                                    input=patch, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_report_keeps_agent_status_separate_from_validation_and_saves_final_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost",
                            "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
            journal = Journal(root, "run-example")
            source.write_text("VALUE = 2\n", encoding="utf-8")
            journal.finish(RunResult("run-example", "done", "completed", 2))
            report = json.loads((journal.directory / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["agent_status"], "completed")
            self.assertEqual(report["validation_status"], "not_checked")
            patch = (journal.directory / "final.patch").read_bytes()
            self.assertIn(b"+VALUE = 2", patch)
            self.assertNotIn(b"\r\n", patch)
