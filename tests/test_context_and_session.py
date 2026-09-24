import tempfile
import unittest
from pathlib import Path

from cobalt.context import EvidenceBook, select_recent_turns
from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.session import SessionStore
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class ModelSequence:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return next(self.turns)


class ContextAndSessionTests(unittest.TestCase):
    def test_context_drops_complete_old_turns_and_keeps_tool_pair(self):
        messages = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "A" * 200},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        ]
        selected, dropped = select_recent_turns(messages, budget_chars=170)
        self.assertEqual(dropped, 1)
        self.assertEqual(selected[1]["content"], "new")
        self.assertEqual(selected[-1]["tool_call_id"], "call-1")

    def test_file_evidence_expires_after_external_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "notes.txt"
            path.write_text("old fact", encoding="utf-8")
            workspace = Workspace(root)
            read = workspace.read_file("notes.txt")
            book = EvidenceBook()
            book.observe("notes.txt", read.digest, read.message)
            self.assertIn("old fact", book.context(workspace))
            path.write_text("new fact", encoding="utf-8")
            context = book.context(workspace)
            self.assertIn("changed", context)
            self.assertNotIn("old fact", context)

    def test_session_resume_keeps_prior_turn_and_uses_same_id(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            gate = ToolGate(workspace, lambda _name, _args: False)
            first_model = ModelSequence([ModelTurn("The file is not present.")])
            first = Agent(workspace, first_model, gate)
            first_result = first.ask("Is there a config file?")
            store = SessionStore(workspace.root)
            self.assertEqual(store.latest(), first_result.session_id)

            next_model = ModelSequence([ModelTurn("The prior question asked about config.")])
            resumed = Agent(workspace, next_model, gate, resume=first_result.session_id)
            next_result = resumed.ask("What did I ask previously?")
            self.assertEqual(next_result.session_id, first_result.session_id)
            self.assertTrue(any(
                message.get("content") == "Is there a config file?"
                for message in next_model.seen[0]
            ))

    def test_resume_marks_unknown_tool_effect_without_repeating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root)

            class InterruptedGate(ToolGate):
                def execute(self, name, args):
                    outcome = super().execute(name, args)
                    if name == "create_file":
                        raise KeyboardInterrupt("simulated crash after write")
                    return outcome

            first = Agent(
                workspace,
                ModelSequence([ModelTurn("", (ToolCall("write-1", "create_file", {
                    "path": "created.txt", "content": "written once",
                }),))]),
                InterruptedGate(workspace, lambda _name, _args: True),
            )
            with self.assertRaises(KeyboardInterrupt):
                first.ask("Create the file")
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "written once")

            stored, _ = SessionStore(root).load(first.session_id)
            self.assertEqual(stored[-1]["role"], "assistant")
            model = ModelSequence([
                ModelTurn("", (ToolCall("inspect-1", "read_file", {"path": "created.txt"}),)),
                ModelTurn("The file exists after the interruption."),
            ])
            resumed = Agent(workspace, model, ToolGate(workspace, lambda _name, _args: False), resume=first.session_id)
            self.assertEqual(resumed.recovered_calls, ["write-1"])
            result = resumed.ask("Check what happened")
            self.assertEqual(result.status, "completed")
            self.assertTrue(any(
                message.get("role") == "tool" and message.get("tool_call_id") == "write-1"
                and "outcome is unknown" in message["content"]
                for message in model.seen[0]
            ))
            self.assertEqual((root / "created.txt").read_text(encoding="utf-8"), "written once")
            events = root / ".cobalt" / "runs" / result.run_id / "events.jsonl"
            self.assertIn("session_recovered", events.read_text(encoding="utf-8"))

    def test_old_session_evidence_does_not_count_as_a_new_repository_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fact.txt").write_text("fact", encoding="utf-8")
            workspace = Workspace(root)
            read = workspace.read_file("fact.txt")
            store = SessionStore(root)
            session_id = store.new_id()
            store.save(session_id, [{"role": "system", "content": "rules"}], {
                "fact.txt": {"sha256": read.digest, "excerpt": read.message},
            })
            agent = Agent(
                workspace, ModelSequence([ModelTurn("The fact is in fact.txt.")]),
                ToolGate(workspace, lambda _name, _args: False), resume=session_id,
            )
            result = agent.ask("Where is the fact?")
            self.assertEqual(result.status, "unverified")
