import json
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.context import DEFAULT_CONTEXT_BUDGET_CHARS
from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.model import ModelOutputError
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class ScriptedModel:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.seen = []
        self.last = None

    def complete(self, messages, tools):
        self.seen.append(list(messages))
        try:
            self.last = next(self.turns)
        except StopIteration:
            # A noncompliant model can repeat its final draft after bounded feedback.
            if self.last is None or self.last.calls:
                raise
        return self.last


class AgentTests(unittest.TestCase):
    def test_false_edit_claim_after_read_is_not_delivered_as_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            model = ScriptedModel([
                ModelTurn("", (ToolCall("read", "read_file", {"path": "service.py"}),)),
                ModelTurn("I modified service.py and fixed the bug."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Fix service.py")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.changed_paths, [])
            self.assertEqual(result.unsupported_action_claims, ["file_change"])
            self.assertNotIn("I modified", result.answer)
            self.assertIn("file_change", result.answer)
            self.assertEqual(result.model_answer, "I modified service.py and fixed the bug.")
            self.assertIn("Remove claims of completed actions", model.seen[2][0]["content"])
            saved = json.loads((root / ".cobalt" / "runs" / result.run_id / "result.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(saved["unsupported_action_claims"], ["file_change"])
            self.assertEqual((root / "service.py").read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_false_successful_check_claim_is_rejected_after_failed_command(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = ScriptedModel([
                ModelTurn("", (ToolCall("run", "run_command", {
                    "argv": [sys.executable, "-c", "raise SystemExit(1)"], "purpose": "check",
                }),)),
                ModelTurn("Tests passed."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Run a check")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unsupported_action_claims, ["successful_check"])
            self.assertNotIn("Tests passed.", result.answer)

    def test_human_edit_during_model_turn_does_not_support_agent_edit_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "service.py"
            file.write_text("VALUE = 1\n", encoding="utf-8")
            workspace = Workspace(root)

            class HumanEdit:
                def __init__(self):
                    self.calls = 0

                def complete(self, _messages, _tools):
                    self.calls += 1
                    if self.calls == 1:
                        return ModelTurn("", (ToolCall("read", "read_file", {"path": "service.py"}),))
                    file.write_text("VALUE = 2\n", encoding="utf-8")
                    return ModelTurn("I modified service.py.")

            result = Agent(workspace, HumanEdit(), ToolGate(workspace, lambda _n, _a: True)).ask("Inspect it")
            self.assertEqual(result.changed_paths, ["service.py"])
            self.assertEqual(result.unsupported_action_claims, ["file_change"])
            self.assertEqual(result.status, "unverified")

    def test_followup_keeps_user_requirement_when_old_logs_can_be_shortened(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            calls = tuple(ToolCall(f"log-{i}", "run_command", {
                "argv": [sys.executable, "-c", "print('diagnostic ' * 1600)"],
            }) for i in range(5))
            model = ScriptedModel([
                ModelTurn("", calls), ModelTurn("Diagnostics collected."),
                ModelTurn("Ready for the next step."),
            ])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True))
            requirement = "Keep the public function name calculate_total unchanged. Collect diagnostics first."
            agent.ask(requirement)
            agent.ask("Continue with the same requirements.")
            view = model.seen[-1]
            self.assertIn(requirement, [m.get("content") for m in view if m["role"] == "user"])
            self.assertLessEqual(len(json.dumps(view, ensure_ascii=False)), DEFAULT_CONTEXT_BUDGET_CHARS)
            requested = {call["id"] for m in view for call in m.get("tool_calls") or []}
            returned = {m["tool_call_id"] for m in view if m["role"] == "tool"}
            self.assertEqual(requested, returned)
            stored, _ = agent.sessions.load(agent.session_id)
            self.assertEqual(sum("diagnostic " * 100 in m.get("content", "")
                                 for m in stored if m["role"] == "tool"), 5)

    def test_archived_failed_commands_can_be_elided_without_losing_failure_status(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            calls = (ToolCall("inspect", "list_files", {}),) + tuple(
                ToolCall(f"failed-{index}", "run_command", {
                    "argv": [sys.executable, "-c", "import sys; print('ERROR: ' + 'x'*20000); sys.exit(7)"],
                }) for index in range(5)
            )
            model = ScriptedModel([ModelTurn("", calls), ModelTurn("All five commands failed.")])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Inspect failed checks")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.verified_commands, [])
            elided = [m["content"] for m in model.seen[-1] if m.get("role") == "tool"
                      and m.get("content", "").startswith("status: elided")]
            self.assertTrue(elided)
            for content in elided:
                self.assertIn("exit_code: 7", content)
                self.assertIn("original_status: error", content)
                self.assertIn("output_id: output-", content)

    def test_many_large_reads_stay_within_model_context_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text(
                "".join(f"{number:04d} " + "x" * 115 + "\n" for number in range(600)),
                encoding="utf-8",
            )
            workspace = Workspace(root)
            calls = tuple(
                ToolCall(f"read-{index}", "read_file", {
                    "path": "large.txt", "start": index * 100 + 1, "lines": 100,
                })
                for index in range(6)
            )
            model = ScriptedModel([ModelTurn("", calls), ModelTurn("I inspected the file.")])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False))
            result = agent.ask("Inspect the full file")
            self.assertEqual(result.tool_calls, 6)
            self.assertLessEqual(len(json.dumps(model.seen[1], ensure_ascii=False)), DEFAULT_CONTEXT_BUDGET_CHARS)
            self.assertEqual(
                {message["tool_call_id"] for message in model.seen[1] if message["role"] == "tool"},
                {f"read-{index}" for index in range(6)},
            )
            self.assertTrue(any(
                message["content"].startswith("status: elided")
                for message in model.seen[1] if message["role"] == "tool"
            ))
            stored, _ = agent.sessions.load(agent.session_id)
            self.assertEqual(
                sum("x" * 115 in message.get("content", "") for message in stored if message["role"] == "tool"),
                6,
            )

    def test_large_command_outputs_keep_results_without_certifying_unchecked_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            calls = tuple(
                ToolCall(f"command-{index}", "run_command", {
                    "argv": [sys.executable, "-c", f"from pathlib import Path; Path('effect-{index}').write_text('done'); print('check-{index}: PASS'); print('x' * 12000)"],
                })
                for index in range(5)
            )
            model = ScriptedModel([ModelTurn("", calls), ModelTurn("The checks ran."), ModelTurn("The checks ran.")])
            agent = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True))
            result = agent.ask("Run the checks")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(len(model.seen), 4)
            self.assertEqual(result.verified_commands, [])
            self.assertEqual(set(result.changed_paths), {f"effect-{index}" for index in range(5)})
            self.assertLessEqual(len(json.dumps(model.seen[1], ensure_ascii=False)), DEFAULT_CONTEXT_BUDGET_CHARS)
            results = [message for message in model.seen[1] if message["role"] == "tool"]
            self.assertEqual(len(results), 5)
            self.assertTrue(any(message["content"].startswith("status: elided") for message in results[:-1]))
            self.assertIn("check-0: PASS", results[0]["content"])
            self.assertIn("x" * 100, results[-1]["content"])
            self.assertTrue(all((workspace.root / f"effect-{index}").read_text() == "done" for index in range(5)))
            stored, _ = agent.sessions.load(agent.session_id)
            self.assertEqual(sum("x" * 100 in message.get("content", "") for message in stored if message["role"] == "tool"), 5)

    def test_elided_read_cannot_support_a_final_source_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text(
                "".join(f"{number:04d} " + "x" * 115 + "\n" for number in range(600)),
                encoding="utf-8",
            )
            workspace = Workspace(root)
            calls = tuple(
                ToolCall(f"read-{index}", "read_file", {
                    "path": "large.txt", "start": index * 100 + 1, "lines": 100,
                })
                for index in range(6)
            )
            model = ScriptedModel([
                ModelTurn("", calls),
                ModelTurn("The answer is in large.txt:1."),
                ModelTurn("The answer is in large.txt:1."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Find the first line")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unsupported_references, ["large.txt:1"])

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
                    "purpose": "check",
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
            self.assertIn("details.py:1", model.seen[2][0]["content"])
            self.assertIn("remove their citations", model.seen[2][0]["content"])

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
            self.assertEqual(gate.execute("run_command", {"argv": ["python", "-V"],
                                                          "purpose": "verified"}).status, "error")
            self.assertEqual(gate.execute("create_file", {"path": "x"}).status, "error")
            self.assertEqual(gate.execute("read_file", {"path": "x", "start": 0}).status, "error")
            self.assertEqual(gate.execute("read_file", {"path": "x", "lines": 401}).status, "error")
            self.assertEqual(gate.execute("run_command", {"argv": ["python"], "timeout": 0}).status, "error")
            read_schema = next(item["function"]["parameters"] for item in gate.schemas()
                               if item["function"]["name"] == "read_file")
            self.assertEqual(read_schema["properties"]["start"]["minimum"], 1)
            self.assertEqual(read_schema["properties"]["lines"]["maximum"], 400)

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

    def test_empty_model_turn_is_retried_and_cannot_finish_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = ScriptedModel([
                ModelTurn("", (ToolCall("list-1", "list_files", {}),)),
                ModelTurn("", prompt_tokens=11, completion_tokens=0),
                ModelTurn("The workspace is empty.", prompt_tokens=13, completion_tokens=5),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Inspect it")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.answer, "The workspace is empty.")
            self.assertEqual(len(model.seen), 3)
            self.assertIn("empty", model.seen[2][0]["content"].lower())
            self.assertEqual(result.prompt_tokens, 24)

    def test_repeated_empty_model_turn_fails_without_claiming_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            model = ScriptedModel([
                ModelTurn("", (ToolCall("list-1", "list_files", {}),)),
                ModelTurn(""), ModelTurn(""), ModelTurn(""),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: False)).ask("Inspect it")
            self.assertEqual(result.status, "model_error")
            self.assertIn("empty", result.answer.lower())
            self.assertEqual(result.tool_calls, 1)
            self.assertEqual(len(model.seen), 4)
