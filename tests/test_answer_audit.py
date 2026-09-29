import tempfile
import unittest
from pathlib import Path

from cobalt.answer_audit import ReadSpan, audit_action_claims, audit_source_references
from cobalt.workspace import Workspace


class AnswerAuditTests(unittest.TestCase):
    def test_claims_of_edits_and_passing_checks_require_observed_evidence(self):
        self.assertEqual(audit_action_claims(
            "### Changes Made\nI modified service.py. Tests passed.",
            changed_paths=set(), checks_run=False, verified_commands=False,
        ), ["file_change", "successful_check"])
        self.assertEqual(audit_action_claims(
            "已经修改代码，测试已通过。", changed_paths={"service.py"},
            checks_run=True, verified_commands=False,
        ), ["successful_check"])

    def test_action_audit_does_not_reject_question_or_quoted_example(self):
        self.assertEqual(audit_action_claims(
            "How can I modify this? I did not modify anything. No changes made. "
            "If tests pass, deploy. `I fixed it`\n"
            "```text\nChanges Made\n```",
            changed_paths=set(), checks_run=False, verified_commands=False,
        ), [])

    def test_inspection_command_does_not_support_a_test_run_claim(self):
        self.assertEqual(audit_action_claims(
            "I ran the tests.", changed_paths=set(), checks_run=False,
            verified_commands=False,
        ), ["check_run"])

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
