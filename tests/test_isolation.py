import subprocess
import tempfile
import unittest
from pathlib import Path

from cobalt.isolation import is_isolated_workspace, prepare_isolated_workspace


class IsolationTests(unittest.TestCase):
    def test_copy_excludes_credentials_and_future_git_history(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "app.py").write_text("version = 1\n", encoding="utf-8")
            (source / ".env").write_text("API_KEY=local-secret\n", encoding="utf-8")
            (source / "nested").mkdir()
            (source / "nested" / ".env.production").write_text("KEY=other-secret\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "add", "app.py"], cwd=source, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost",
                            "commit", "-qm", "Initial"], cwd=source, check=True, capture_output=True)
            (source / "app.py").write_text("version = 2\n", encoding="utf-8")
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost",
                            "commit", "-qam", "Future fix"],
                           cwd=source, check=True, capture_output=True)

            target = prepare_isolated_workspace(source)
            self.assertTrue(is_isolated_workspace(target))
            self.assertFalse((target / ".env").exists())
            self.assertFalse((target / "nested" / ".env.production").exists())
            self.assertEqual((target / "app.py").read_text(encoding="utf-8"), "version = 2\n")
            history = subprocess.check_output(["git", "log", "--oneline"], cwd=target, text=True)
            self.assertEqual(len(history.splitlines()), 1)
            self.assertNotIn("Future fix", history)
            self.assertEqual(subprocess.check_output(["git", "config", "--local", "--get",
                                                      "core.autocrlf"], cwd=target, text=True).strip(), "false")
            self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"],
                                                     cwd=target, text=True), "")
