import sys
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
            book.observe("notes.txt", read.digest, start=1, lines=1)
            self.assertIn("requested lines 1-1", book.context(workspace))
            self.assertNotIn("old fact", book.context(workspace))
            path.write_text("new fact", encoding="utf-8")
            context = book.context(workspace)
            self.assertIn("changed", context)
            self.assertNotIn("old fact", context)

    def test_evidence_index_retains_read_locations_without_fake_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("private detail\nother detail\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.file_digest("notes.txt")
            book = EvidenceBook()
            book.observe("notes.txt", digest, start=1, lines=1)
            book.observe("notes.txt", digest, start=2, lines=1)
            hint = book.context(workspace)
            self.assertIn("1-1, 2-2", hint)
            self.assertNotIn("private detail", hint)
            self.assertEqual(len(book.observations["notes.txt"]["ranges"]), 2)
            legacy = EvidenceBook({"notes.txt": {"sha256": digest, "excerpt": "old truncated text"}})
            self.assertIn("location index", legacy.context(workspace))
            self.assertNotIn("old truncated text", legacy.context(workspace))

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

    def test_resume_does_not_blindly_replay_an_interrupted_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root)
            command = [sys.executable, "-c", "from pathlib import Path; p=Path('count.txt'); p.write_text(str(int(p.read_text())+1 if p.exists() else 1))"]

            class CrashAfterCommand(ToolGate):
                def execute(self, name, args):
                    outcome = super().execute(name, args)
                    if name == "run_command":
                        raise KeyboardInterrupt("simulated crash after command")
                    return outcome

            first = Agent(
                workspace,
                ModelSequence([ModelTurn("", (ToolCall("cmd-1", "run_command", {"argv": command}),))]),
                CrashAfterCommand(workspace, lambda _name, _args: True),
            )
            with self.assertRaises(KeyboardInterrupt):
                first.ask("Increment the counter")
            self.assertEqual((root / "count.txt").read_text(), "1")

            resumed_model = ModelSequence([
                ModelTurn("", (ToolCall("cmd-2", "run_command", {"argv": command}),)),
                ModelTurn("", (ToolCall("inspect", "read_file", {"path": "count.txt"}),)),
                ModelTurn("The counter is 1 in count.txt:1."),
            ])
            resumed = Agent(
                workspace, resumed_model, ToolGate(workspace, lambda _name, _args: True),
                resume=first.session_id,
            )
            result = resumed.ask("Check whether the command ran")
            self.assertEqual((root / "count.txt").read_text(), "1")
            self.assertEqual(result.status, "completed")
            self.assertIn("status: denied", resumed_model.seen[1][-1]["content"])
            restarted = Agent(
                workspace, ModelSequence([ModelTurn("Inspected.")]),
                ToolGate(workspace, lambda _name, _args: True), resume=first.session_id,
            )
            self.assertEqual(restarted.recovered_calls, [])

    def test_inspection_must_be_seen_by_model_before_recovery_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "count.txt").write_text("1", encoding="utf-8")
            workspace = Workspace(root)
            store = SessionStore(root)
            session_id = store.new_id()
            store.save(session_id, [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "Run once"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "old", "function": {"name": "run_command", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "old", "content": "status: interrupted\nExecution outcome is unknown"},
            ], {})
            command = [sys.executable, "-c", "from pathlib import Path; p=Path('count.txt'); p.write_text(str(int(p.read_text())+1))"]
            model = ModelSequence([
                ModelTurn("", (
                    ToolCall("inspect", "read_file", {"path": "count.txt"}),
                    ToolCall("retry", "run_command", {"argv": command}),
                )),
                ModelTurn("Current count is 1 in count.txt:1."),
            ])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _name, _args: True), resume=session_id)
            result = agent.ask("Check the outcome before trying again")
            self.assertEqual((root / "count.txt").read_text(), "1")
            self.assertEqual(result.status, "completed")
            self.assertIn("status: denied", model.seen[1][-1]["content"])

    def test_second_restart_keeps_barrier_until_model_sees_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "count.txt").write_text("1", encoding="utf-8")
            workspace = Workspace(root)
            store = SessionStore(root)
            session_id = store.new_id()
            store.save(session_id, [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "Run once"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "old", "function": {"name": "run_command", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "old", "content": "status: interrupted\nExecution outcome is unknown"},
                {"role": "user", "content": "Inspect the result"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "read", "function": {"name": "read_file", "arguments": '{"path":"count.txt"}'}}]},
                {"role": "tool", "tool_call_id": "read", "content": "status: ok\n   1 1\npath: count.txt"},
                {"role": "user", "content": "An unanswered oversized request: " + "x" * 47900},
            ], {})
            command = [sys.executable, "-c", "from pathlib import Path; p=Path('count.txt'); p.write_text(str(int(p.read_text())+1))"]
            model = ModelSequence([
                ModelTurn("", (ToolCall("retry", "run_command", {"argv": command}),)),
                ModelTurn("", (ToolCall("inspect-again", "read_file", {"path": "count.txt"}),)),
                ModelTurn("Count is 1 in count.txt:1."),
            ])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _name, _args: True), resume=session_id)
            agent.ask("Check the interrupted command")
            self.assertEqual((root / "count.txt").read_text(), "1")
            self.assertFalse(any(message.get("tool_call_id") == "read" for message in model.seen[0]))
            self.assertIn("status: denied", model.seen[1][-1]["content"])

    def test_old_session_evidence_does_not_count_as_a_new_repository_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fact.txt").write_text("fact", encoding="utf-8")
            workspace = Workspace(root)
            read = workspace.read_file("fact.txt")
            store = SessionStore(root)
            session_id = store.new_id()
            store.save(session_id, [{"role": "system", "content": "rules"}], {
                "fact.txt": {"sha256": read.digest, "ranges": [[1, 1]]},
            })
            agent = Agent(
                workspace, ModelSequence([ModelTurn("The fact is in fact.txt.")]),
                ToolGate(workspace, lambda _name, _args: False), resume=session_id,
            )
            result = agent.ask("Where is the fact?")
            self.assertEqual(result.status, "unverified")
