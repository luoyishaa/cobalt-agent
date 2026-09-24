import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.context import prepare_context
from cobalt.outputs import OutputStore
from cobalt.workspace import Workspace
from scripts.evaluate_history_outputs import grade_recall


class HistoryOutputTests(unittest.TestCase):
    def test_recall_grader_requires_exact_observed_result_and_single_execution(self):
        self.assertTrue(grade_recall("RESULT=abc EXIT=7", "abc", 7, 1, [], True))
        for answer, count, actions, evidence in [
            ("RESULT=abcd EXIT=7", 1, [], True),
            ("RESULT=abc EXIT=0", 1, [], True),
            ("RESULT=abc EXIT=7", 2, [], True),
            ("RESULT=abc EXIT=7", 1, [], False),
            ("RESULT=abc EXIT=7", 1, [{"name": "run_command"}], True),
            ("RESULT=abc EXIT=7; RESULT=wrong EXIT=7", 1, [], True),
        ]:
            with self.subTest(answer=answer, count=count, actions=actions, evidence=evidence):
                self.assertFalse(grade_recall(answer, "abc", 7, count, actions, evidence))

    def test_current_output_and_unarchived_history_keep_their_content(self):
        for archived, current in ((True, True), (False, False)):
            with self.subTest(archived=archived, current=current):
                content = "status: ok\nexit_code: 0\n" + ("output_id: output-example\n" if archived else "") + "detail " * 600
                messages = [
                    {"role": "system", "content": "rules"},
                    {"role": "user", "content": "Inspect"},
                    {"role": "assistant", "tool_calls": [{
                        "id": "one", "function": {"name": "run_command", "arguments": "{}"},
                    }]},
                    {"role": "tool", "tool_call_id": "one", "content": content},
                ]
                if not current:
                    messages.extend([{"role": "assistant", "content": "Done"},
                                     {"role": "user", "content": "Continue"}])
                view, _, _, commands = prepare_context(messages, compact_history=True)
                self.assertEqual(view[3]["content"], content)
                self.assertEqual(commands, [])

    def test_old_archived_output_is_shortened_and_readable_without_reexecution(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                workspace = Workspace(Path(directory))
                outcome = workspace.run_command([sys.executable, "-c",
                    f"import sys; print('x'*2000); print('RESULT=one-time'); print('y'*2000); sys.exit({exit_code})"])
                body = outcome.to_message()
                locator = next(line for line in body.splitlines() if line.startswith("output_id: "))
                messages = [
                    {"role": "system", "content": "rules"},
                    {"role": "user", "content": "Keep my requirement."},
                    {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "old", "function": {"name": "run_command", "arguments": "{}"},
                    }]},
                    {"role": "tool", "tool_call_id": "old", "content": body},
                    {"role": "assistant", "content": "Recorded."},
                    {"role": "user", "content": "Continue."},
                ]
                view, dropped, reads, commands = prepare_context(messages, compact_history=True)
                self.assertEqual(commands, ["old"])
                self.assertEqual((dropped, reads), (0, []))
                self.assertEqual(view[1]["content"], "Keep my requirement.")
                self.assertIn(f"exit_code: {exit_code}", view[3]["content"])
                self.assertIn(locator, view[3]["content"])
                self.assertNotIn("RESULT=one-time", view[3]["content"])
                self.assertEqual(messages[3]["content"], body)
                saved = OutputStore(workspace.root).read(locator.split(": ")[1], query="RESULT=")
                self.assertIn("RESULT=one-time", saved.message)
