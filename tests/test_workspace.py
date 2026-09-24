import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cobalt.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_crlf_request_keeps_lf_file_and_single_line_insertion_keeps_crlf(self):
        for before, old, new, expected in [
            (b"a\nb\n", "a\r\nb", "c\r\nd", b"c\nd\n"),
            (b"a\r\nb\r\n", "b", "c\nd", b"a\r\nc\r\nd\r\n"),
        ]:
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "text.txt"
                path.write_bytes(before)
                workspace = Workspace(Path(directory))
                workspace.replace_text("text.txt", old, new, workspace.read_file("text.txt").digest)
                self.assertEqual(path.read_bytes(), expected)

    def test_newline_compatibility_does_not_allow_ambiguous_or_inexact_edits(self):
        for before, old in [
            (b"a\r\nb\r\na\r\nb\r\n", "a\nb"),
            (b"a\r\n b\r\n", "a\nb"),
        ]:
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "text.txt"
                path.write_bytes(before)
                workspace = Workspace(Path(directory))
                with self.assertRaisesRegex(ValueError, "exactly once"):
                    workspace.replace_text("text.txt", old, "changed", workspace.read_file("text.txt").digest)
                self.assertEqual(path.read_bytes(), before)

    def test_mixed_newlines_are_not_silently_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.txt"
            original = b"a\r\nb\nc\r\n"
            path.write_bytes(original)
            workspace = Workspace(Path(directory))
            with self.assertRaisesRegex(ValueError, "mixed"):
                workspace.replace_text("text.txt", "a\nb", "new", workspace.read_file("text.txt").digest)
            self.assertEqual(path.read_bytes(), original)

    def test_multiline_edit_preserves_crlf_when_request_uses_lf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.py"
            path.write_bytes(b"# untouched\r\ndef total(amounts):\r\n    raise NotImplementedError\r\n")
            workspace = Workspace(Path(directory))
            read = workspace.read_file("task.py")
            workspace.replace_text("task.py", "def total(amounts):\n    raise NotImplementedError",
                                   "def total(amounts):\n    return sum(amounts)", read.digest)
            self.assertEqual(path.read_bytes(), b"# untouched\r\ndef total(amounts):\r\n    return sum(amounts)\r\n")

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

    def test_edit_keeps_existing_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool.py"
            path.write_text("value = 1\n", encoding="utf-8")
            path.chmod(0o755)
            original_mode = path.stat().st_mode & 0o777
            workspace = Workspace(Path(directory))
            digest = workspace.read_file("tool.py").digest
            workspace.replace_text("tool.py", "value = 1", "value = 2", digest)
            self.assertEqual(path.stat().st_mode & 0o777, original_mode)

    def test_create_file_never_overwrites_an_existing_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("human content", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                Workspace(Path(directory)).create_file("notes.txt", "agent content")
            self.assertEqual(path.read_text(encoding="utf-8"), "human content")

    def test_command_timeout_stops_the_process(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            started = time.monotonic()
            result = workspace.run_command([sys.executable, "-c", "import time; time.sleep(10)"], timeout=1)
            self.assertEqual(result.status, "error")
            self.assertIn("timed out", result.message)
            self.assertLess(time.monotonic() - started, 6)
