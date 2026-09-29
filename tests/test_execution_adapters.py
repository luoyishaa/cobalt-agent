import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cobalt.domain import ToolOutcome
from cobalt.execution import DockerCommandRunner
from cobalt.isolation import prepare_isolated_workspace
from cobalt.workspace import Workspace


class DockerExecutionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("COBALT_RUN_DOCKER_TESTS") == "1", "requires a local Docker image")
    def test_real_container_hides_local_secret_and_enforces_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "task"
            source.mkdir()
            (Path(directory) / "outside-secret.txt").write_text("outside", encoding="utf-8")
            (source / ".env").write_text("DEEPSEEK_API_KEY=fixture-secret\n", encoding="utf-8")
            (source / "app.py").write_bytes(b"VALUE = 1\r\n")
            isolated = prepare_isolated_workspace(source)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixture-secret"}):
                runner = DockerCommandRunner(isolated, os.environ.get("COBALT_TEST_IMAGE", "cobalt/python:3.11"))
                check = runner.run(["python", "-c", "import os, pathlib; print('KEY_VISIBLE=' + str('DEEPSEEK_API_KEY' in os.environ)); print('FILE_VISIBLE=' + str(pathlib.Path('/workspace/.env').exists())); print('OUTSIDE_VISIBLE=' + str(pathlib.Path('/workspace/../outside-secret.txt').exists()))"], timeout=15)
                git_status = runner.run(["git", "status", "--porcelain"], timeout=15)
                timeout = runner.run(["python", "-c", "import time; time.sleep(10)"], timeout=2)
            self.assertEqual(check.status, "ok", check.message)
            self.assertIn("KEY_VISIBLE=False", check.message)
            self.assertIn("FILE_VISIBLE=False", check.message)
            self.assertIn("OUTSIDE_VISIBLE=False", check.message)
            self.assertEqual(git_status.status, "ok", git_status.message)
            self.assertIn("(no output)", git_status.message)
            self.assertEqual(timeout.status, "error")
            self.assertIn("timed out", timeout.message)

    def test_container_command_has_explicit_limits_one_mount_and_no_model_key(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = {}

            def probe(argv, _message):
                return b"linux" if argv[1] == "info" else b"sha256:example"

            def fake_run(root, argv, *, timeout, env, on_timeout):
                captured.update(root=root, argv=argv, timeout=timeout, env=env,
                                on_timeout=on_timeout)
                return ToolOutcome("ok", "done")

            with (patch.object(DockerCommandRunner, "_probe", staticmethod(probe)),
                  patch("cobalt.execution._run_process", side_effect=fake_run),
                  patch.dict(os.environ, {"DEEPSEEK_API_KEY": "never-send-me"})):
                runner = DockerCommandRunner(Path(directory), "example:1", container_env={"LANG": "C.UTF-8"})
                self.assertIn("Workspace: /workspace", Workspace(Path(directory), command_runner=runner).overview())
                outcome = runner.run(["python", "-V"], timeout=5)
            self.assertEqual(outcome.status, "ok")
            command = captured["argv"]
            self.assertEqual(command.count("--mount"), 1)
            for flag in ("--network=none", "--read-only", "--memory=2g", "--memory-swap=2g",
                         "--cpus=2", "--pids-limit=256", "--cap-drop=ALL",
                         "--security-opt=no-new-privileges", "--pull=never"):
                self.assertIn(flag, command)
            self.assertEqual(command[-3:], ["example:1", "python", "-V"])
            self.assertNotIn("never-send-me", str(command))
            self.assertNotIn("DEEPSEEK_API_KEY", captured["env"])

    def test_container_env_rejects_secret_like_names(self):
        with (tempfile.TemporaryDirectory() as directory,
              self.assertRaisesRegex(ValueError, "secret-like")):
            DockerCommandRunner(Path(directory), "example:1",
                                container_env={"OPENAI_API_KEY": "bad"})
