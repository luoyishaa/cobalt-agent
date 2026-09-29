import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cobalt.cli import _approval, main
from cobalt.domain import RunResult
from cobalt.journal import Journal


class CliExecutionTests(unittest.TestCase):
    def test_local_action_defaults_to_denied_until_individually_approved(self):
        with patch("builtins.input", return_value=""):
            self.assertFalse(_approval(False)("run_command", {"argv": ["python", "-V"]}))
        with patch("builtins.input", return_value="y"):
            self.assertTrue(_approval(False)("run_command", {"argv": ["python", "-V"]}))

    def test_failed_user_check_sets_exit_code_without_changing_agent_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_file = root / "task.json"
            task_file.write_text(json.dumps({
                "request": "repair behavior",
                "checks": [[sys.executable, "-c", "raise SystemExit(1)"]],
                "protected_paths": [],
            }), encoding="utf-8")

            class FinishedAgent:
                def __init__(self, workspace, *_args, **_kwargs):
                    self.workspace = workspace

                def ask(self, _question):
                    result = RunResult("run-012345abcdef", "finished", "completed", 0)
                    Journal(self.workspace.root, result.run_id).finish(result)
                    return result

            output = StringIO()
            with (patch("cobalt.cli.resolve_config", return_value=object()),
                  patch("cobalt.cli.from_config", return_value=object()),
                  patch("cobalt.cli.Agent", FinishedAgent),
                  patch("builtins.input", return_value="y"),
                  redirect_stdout(output)):
                code = main(["--workspace", directory, "--task-file", str(task_file)])
            self.assertEqual(code, 1)
            self.assertIn("Execution: completed; validation: failed (user_specified)", output.getvalue())

    def test_automatic_approval_requires_container_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as caught:
                main(["--workspace", directory, "--yes", "inspect this repository"])
            self.assertEqual(caught.exception.code, 2)

    def test_container_mode_refuses_missing_docker_before_model_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("DEEPSEEK_API_KEY=never-share-this-value\n", encoding="utf-8")
            outcome = main(["--workspace", directory, "--execution", "container",
                            "--image", "cobalt-test-missing:latest", "inspect this repository"])
            self.assertEqual(outcome, 2)

    def test_container_mode_does_not_fall_back_when_docker_engine_is_unavailable(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch("cobalt.cli.DockerCommandRunner", side_effect=ValueError("Docker engine is unavailable")),
              patch("cobalt.cli.resolve_config") as config):
            outcome = main(["--workspace", directory, "--execution", "container",
                            "--yes", "inspect this repository"])
            self.assertEqual(outcome, 2)
            config.assert_not_called()

    def test_saved_report_is_visible_without_model_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = Journal(root, "run-012345abcdef")
            journal.finish(RunResult("run-012345abcdef", "done", "completed", 0))
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--workspace", directory, "--report", "run-012345abcdef"])
            self.assertEqual(code, 0)
            self.assertIn("Execution: completed", output.getvalue())
            self.assertIn("Validation: not_checked", output.getvalue())
