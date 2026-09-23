import tempfile
import unittest
from pathlib import Path

from cobalt.answer_audit import ReadSpan, audit_source_references
from cobalt.workspace import Workspace


class AnswerAuditTests(unittest.TestCase):
    def test_only_fresh_observed_line_ranges_support_explicit_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "src" / "module.py"
            file.parent.mkdir()
            file.write_text("one\ntwo\nthree\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("src/module.py").digest
            reads = [ReadSpan("src/module.py", 1, 2, digest)]

            cited, unsupported = audit_source_references(
                "See src/module.py:1-2, module.py:3 and missing.py:4.", reads, workspace,
            )
            self.assertEqual(cited, ["src/module.py:1-2", "module.py:3", "missing.py:4"])
            self.assertEqual(unsupported, ["module.py:3", "missing.py:4"])
