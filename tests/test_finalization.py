import json
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class Sequence:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return next(self.turns, ModelTurn("Everything is verified."))


def create():
    return ModelTurn("", (ToolCall("create", "create_file", {"path": "check.py", "content": "assert 1 == 1\n"}),))


def check():
    return ModelTurn("", (ToolCall("check", "run_command", {"argv": [sys.executable, "check.py"]}),))


def read():
    return ModelTurn("", (ToolCall("read", "read_file", {"path": "check.py"}),))


class FinalizationTests(unittest.TestCase):
    def test_partial_progress_does_not_reset_the_finalization_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = Sequence([
                create(),
                ModelTurn("", (ToolCall("second", "create_file", {"path": "other.py", "content": "pass\n"}),)),
                ModelTurn("Everything is verified."), read(), ModelTurn("Everything is verified."),
                ModelTurn("", (ToolCall("other-read", "read_file", {"path": "other.py"}),)),
                ModelTurn("Everything is verified."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Create checks")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(len(model.seen), 7)
            self.assertEqual(result.unrefreshed_paths, [])
            self.assertIn("no successful command", result.answer)

    def test_missing_read_and_check_can_be_completed_within_original_tool_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = Sequence([create(), ModelTurn("Everything is verified."), read(), check(), ModelTurn("Checked.")])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True), max_tool_calls=4).ask("Create a check")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.tool_calls, 3)
            self.assertEqual(result.answer, "Checked.")
            feedback = model.seen[2][0]["content"]
            self.assertIn("check.py", feedback)
            self.assertIn("Run a relevant successful check", feedback)
            self.assertIn("Everything is verified.", feedback)

    def test_normal_completion_has_no_extra_model_round(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = Sequence([create(), read(), check(), ModelTurn("Checked.")])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Create a check")
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(model.seen), 4)

    def test_tool_limit_does_not_expand_to_satisfy_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = Sequence([create(), ModelTurn("Everything is verified."), check(), read()])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True), max_tool_calls=2).ask("Create a check")
            self.assertEqual(result.status, "limit")
            self.assertEqual(result.tool_calls, 2)
            self.assertNotIn("Everything is verified", result.answer)
            self.assertEqual(result.unrefreshed_paths, ["check.py"])

    def test_unresolved_draft_is_retained_but_not_delivered_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = Sequence([create(), check()])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True))
            result = agent.ask("Create and inspect a check")
            self.assertEqual(result.status, "unverified")
            self.assertTrue(result.answer.startswith("Unverified"))
            self.assertNotIn("Everything is verified", result.answer)
            self.assertIn("check.py", result.answer)
            self.assertEqual(result.model_answer, "Everything is verified.")
            self.assertEqual(len(model.seen), 5)  # Two tools plus three final attempts.
            self.assertEqual(agent.messages[-1]["content"], result.answer)
            saved = json.loads((workspace.root / ".cobalt" / "runs" / result.run_id / "result.json").read_text())
            self.assertEqual(saved["model_answer"], "Everything is verified.")
            self.assertEqual(saved["answer"], result.answer)
