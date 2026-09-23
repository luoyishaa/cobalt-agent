import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from cobalt.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_symlink_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "repo"
            root.mkdir()
            outside = parent / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            try:
                os.symlink(outside, root / "link.txt")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaisesRegex(ValueError, "escapes"):
                Workspace(root).read_file("link.txt")

    def test_stale_edit_does_not_overwrite_a_human_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "task.py"
            path.write_text("value = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("task.py").digest
            path.write_text("value = 3\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed since it was read"):
                workspace.replace_text("task.py", "value = 1", "value = 2", digest)
            self.assertEqual(path.read_text(encoding="utf-8"), "value = 3\n")

    def test_path_escape_and_private_configuration_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("DEEPSEEK_API_KEY=private", encoding="utf-8")
            workspace = Workspace(root)
            with self.assertRaisesRegex(ValueError, "escapes"):
                workspace.read_file("../outside.txt")
            with self.assertRaisesRegex(ValueError, "private"):
                workspace.read_file(".env")
            self.assertNotIn(".env", workspace.list_files().message)
            self.assertNotIn("private", workspace.search("private").message)

    def test_edit_uses_digest_and_reports_new_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.py").write_text("value = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("task.py").digest
            result = workspace.replace_text("task.py", "value = 1", "value = 2", digest)
            self.assertTrue(result.changed)
            self.assertEqual(result.digest, hashlib.sha256((root / "task.py").read_bytes()).hexdigest())
