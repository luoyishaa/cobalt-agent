import json
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.model import ModelOutputError
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class ScriptedModel:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(list(messages))
        return next(self.turns)


class AgentTests(unittest.TestCase):
    def test_read_edit_verify_flow_has_report_and_real_command_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.py").write_text("value = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("task.py").digest
            model = ScriptedModel([
                ModelTurn("", (ToolCall("read-1", "read_file", {"path": "task.py"}),)),
                ModelTurn("", (ToolCall("edit-1", "replace_text", {
                    "path": "task.py", "old": "value = 1", "new": "value = 2", "expected_sha256": digest,
                }),)),
                ModelTurn("", (ToolCall("verify-1", "run_command", {
                    "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('task.py').read_text() == 'value = 2\\n'"],
                }),)),
                ModelTurn("Updated and checked."),
                ModelTurn("", (ToolCall("read-after-edit", "read_file", {"path": "task.py"}),)),
                ModelTurn("Updated and checked."),
            ])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True))
            result = agent.ask("Update task.py and verify it")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.changed_paths, ["task.py"])
            self.assertEqual(len(result.verified_commands), 1)
            events = root / ".cobalt" / "runs" / result.run_id / "events.jsonl"
            self.assertIn("tool_finished", events.read_text(encoding="utf-8"))
            self.assertIn("post_edit_read_completed", events.read_text(encoding="utf-8"))
            report = json.loads((events.parent / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "completed")
            self.assertEqual(model.seen[1][-1]["tool_call_id"], "read-1")

    def test_edit_without_verification_is_reported_as_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.py").write_text("before\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("task.py").digest
            model = ScriptedModel([
                ModelTurn("", (ToolCall("edit", "replace_text", {
                    "path": "task.py", "old": "before", "new": "after", "expected_sha256": digest,
                }),)),
                ModelTurn("", (ToolCall("read-after-edit", "read_file", {"path": "task.py"}),)),
                ModelTurn("Done."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Edit it")
            self.assertEqual(result.status, "unverified")
            self.assertIn("no successful command", result.answer)

    def test_approval_denial_keeps_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "task.py").write_text("before\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.read_file("task.py").digest
            model = ScriptedModel([
                ModelTurn("", (ToolCall("edit", "replace_text", {
                    "path": "task.py", "old": "before", "new": "after", "expected_sha256": digest,
                }),)),
                ModelTurn("I could not edit the file."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Edit it")
            self.assertEqual((root / "task.py").read_text(encoding="utf-8"), "before\n")
            self.assertEqual(result.changed_paths, [])

    def test_answer_without_repository_action_is_labeled_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            result = Agent(
                workspace, ScriptedModel([ModelTurn("I read the entrypoint and found main")]),
                ToolGate(workspace, lambda _n, _a: False),
            ).ask("Where is the entrypoint?")
            self.assertEqual(result.status, "unverified")
            self.assertIn("no repository tool ran", result.answer)

    def test_answer_with_unread_source_location_is_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actual.py").write_text("VALUE = 8080\n", encoding="utf-8")
            workspace = Workspace(root)
            model = ScriptedModel([
                ModelTurn("", (ToolCall("read-1", "read_file", {"path": "actual.py"}),)),
                ModelTurn("The value is 8080 in `missing.py:46`."),
                ModelTurn("The value is 8080 in `missing.py:46`."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Find the value")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unsupported_references, ["missing.py:46"])
            self.assertIn("not backed by a fresh read", result.answer)

    def test_answer_location_must_be_within_fresh_lines_read_this_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "actual.py"
            path.write_text("first\nsecond\n", encoding="utf-8")
            workspace = Workspace(root)

            class ChangingModel:
                def __init__(self):
                    self.calls = 0

                def complete(self, messages, tools):
                    self.calls += 1
                    if self.calls == 1:
                        return ModelTurn("", (ToolCall("read-1", "read_file", {"path": "actual.py", "lines": 1}),))
                    path.write_text("changed\nsecond\n", encoding="utf-8")
                    return ModelTurn("See actual.py:1 and actual.py:2.")

            result = Agent(workspace, ChangingModel(), ToolGate(workspace, lambda _n, _a: False)).ask("Read it")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unsupported_references, ["actual.py:1", "actual.py:2"])

    def test_answer_audit_gives_one_chance_to_read_a_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actual.py").write_text("VALUE = 8080\n", encoding="utf-8")
            (root / "details.py").write_text("NAME = 'port'\n", encoding="utf-8")
            workspace = Workspace(root)
            model = ScriptedModel([
                ModelTurn("", (ToolCall("read-1", "read_file", {"path": "actual.py"}),)),
                ModelTurn("See actual.py:1 and details.py:1."),
                ModelTurn("", (ToolCall("read-2", "read_file", {"path": "details.py"}),)),
                ModelTurn("See actual.py:1 and details.py:1."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Find the port")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.unsupported_references, [])
            self.assertIn("not read in this request", model.seen[2][0]["content"])

    def test_read_only_mode_hides_and_denies_write_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root)
            gate = ToolGate(workspace, lambda _n, _a: True, read_only=True)
            self.assertNotIn("create_file", [schema["function"]["name"] for schema in gate.schemas()])
            outcome = gate.execute("create_file", {"path": "new.py", "content": "pass"})
            self.assertEqual(outcome.status, "denied")
            self.assertFalse((root / "new.py").exists())

    def test_tool_gate_rejects_unknown_and_wrong_type_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = ToolGate(Workspace(Path(directory)), lambda _n, _a: True)
            self.assertEqual(gate.execute("read_file", {"path": "x", "surprise": 1}).status, "error")
            self.assertEqual(gate.execute("run_command", {"argv": "python -V"}).status, "error")
            self.assertEqual(gate.execute("create_file", {"path": "x"}).status, "error")

    def test_malformed_model_response_retries_with_a_bounded_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))

            class RecoveringModel:
                def __init__(self):
                    self.prompts = []

                def complete(self, messages, tools):
                    self.prompts.append(messages)
                    if len(self.prompts) == 1:
                        raise ModelOutputError("invalid tool JSON")
                    if len(self.prompts) == 2:
                        return ModelTurn("", (ToolCall("list-1", "list_files", {}),))
                    return ModelTurn("The workspace is empty.")

            model = RecoveringModel()
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Inspect the workspace")
            self.assertEqual(result.status, "completed")
            self.assertIn("last response contained invalid", model.prompts[1][0]["content"].lower())
            self.assertNotIn("last response contained invalid", model.prompts[2][0]["content"].lower())
