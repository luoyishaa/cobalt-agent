import json
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.snapshots import Snapshot
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class Script:
    def __init__(self, turns):
        self.turns = iter(turns)

    def complete(self, messages, tools):
        return next(self.turns, ModelTurn("The work is verified."))


def command(call_id, code, *, purpose="inspect"):
    return ModelTurn("", (ToolCall(call_id, "run_command", {
        "argv": [sys.executable, "-c", code], "purpose": purpose,
    }),))


def read(path):
    return ModelTurn("", (ToolCall("read-" + path, "read_file", {"path": path}),))


class VersionEvidenceTests(unittest.TestCase):
    def test_guarded_write_readback_shows_the_changed_region(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.py"
            source.write_text("".join(f"VALUE_{index} = {index}\n" for index in range(250)),
                              encoding="utf-8")
            workspace = Workspace(root)
            gate = ToolGate(workspace, lambda _n, _a: True)
            outcome = gate.execute("replace_text", {
                "path": "large.py", "old": "VALUE_190 = 190", "new": "VALUE_190 = 999",
                "expected_sha256": workspace.file_digest("large.py"),
            })
            self.assertEqual(outcome.status, "ok")
            self.assertIsNotNone(outcome.readback)
            self.assertEqual(outcome.readback.start, 191)
            self.assertIn("VALUE_190 = 999", outcome.to_message())
            self.assertEqual(outcome.readback.digest, workspace.file_digest("large.py"))

    def test_concurrent_change_after_write_readback_keeps_refresh_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "value.py"
            path.write_text("VALUE = 1\n", encoding="utf-8")

            class RacingWorkspace(Workspace):
                def read_file(self, relative, **kwargs):
                    outcome = super().read_file(relative, **kwargs)
                    if relative == "value.py" and "VALUE = 2" in outcome.message:
                        path.write_text("VALUE = 3\n", encoding="utf-8")
                    return outcome

            workspace = RacingWorkspace(root)
            result = Agent(workspace, Script([
                read("value.py"),
                ModelTurn("", (ToolCall("edit", "replace_text", {
                    "path": "value.py", "old": "VALUE = 1", "new": "VALUE = 2",
                    "expected_sha256": workspace.file_digest("value.py"),
                }),)),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Change to 2")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unrefreshed_paths, ["value.py"])
            self.assertEqual(path.read_text(encoding="utf-8"), "VALUE = 3\n")

    def test_guarded_write_reads_back_its_persisted_edit_before_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            digest = workspace.file_digest("value.py")
            model = Script([
                read("value.py"),
                ModelTurn("", (ToolCall("edit", "replace_text", {
                    "path": "value.py", "old": "VALUE = 1", "new": "VALUE = 2",
                    "expected_sha256": digest,
                }),)),
                command("check", "from value import VALUE; assert VALUE == 2", purpose="check"),
                ModelTurn("I modified value.py. Tests passed."),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Change to 2")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.unrefreshed_paths, [])
            self.assertEqual(len(result.verified_commands), 1)
            self.assertEqual((root / "value.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            events = (root / ".cobalt" / "runs" / result.run_id / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"source": "write_readback"', events)

    def test_read_changed_before_delivery_does_not_clear_refresh_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("0")

            class ConcurrentWriter(Workspace):
                def read_file(self, relative, **kwargs):
                    outcome = super().read_file(relative, **kwargs)
                    (self.root / relative).write_text("2")
                    return outcome

            workspace = ConcurrentWriter(root)
            result = Agent(workspace, Script([
                command("edit", "from pathlib import Path; Path('value.txt').write_text('1')"),
                read("value.txt"),
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '2'",
                        purpose="check"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Edit and inspect current contents")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.unrefreshed_paths, ["value.txt"])

    def test_creation_modification_and_deletion_are_reported_after_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "modified.txt").write_text("old")
            (root / "deleted.txt").write_text("old")
            gate = ToolGate(Workspace(root), lambda _n, _a: True)
            outcome = gate.execute("run_command", {"argv": [sys.executable, "-c",
                ("from pathlib import Path; Path('modified.txt').write_text('new'); "
                 "Path('deleted.txt').unlink(); Path('created.txt').write_text('new'); raise SystemExit(7)")]})
            self.assertEqual(outcome.status, "error")
            self.assertFalse(outcome.verified)
            self.assertEqual(outcome.changes, {"created.txt": "created", "deleted.txt": "deleted", "modified.txt": "modified"})
            self.assertTrue(outcome.changed)

    def test_fresh_successful_check_and_reread_allow_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("1")
            workspace = Workspace(root)
            result = Agent(workspace, Script([
                command("edit", "from pathlib import Path; Path('value.txt').write_text('2')"),
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '2'",
                        purpose="check"),
                read("value.txt"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Change to 2 and check")
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(result.verified_commands), 1)
            self.assertEqual(result.verification_fingerprint, workspace.snapshot().fingerprint)

    def test_successful_inspection_does_not_certify_an_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("1")
            workspace = Workspace(root)
            result = Agent(workspace, Script([
                command("edit", "from pathlib import Path; Path('value.txt').write_text('2')"),
                read("value.txt"),
                command("inspect", "from pathlib import Path; print(Path('value.txt').read_text())"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Change to 2 and check")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.verified_commands, [])
            report = json.loads((root / ".cobalt" / "runs" / result.run_id / "report.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(report["validation_status"], "not_checked")
            self.assertEqual(report["checks_after_last_change"], [])

    def test_deleted_file_needs_a_new_check_but_not_an_impossible_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "obsolete.txt").write_text("old")
            workspace = Workspace(root)
            result = Agent(workspace, Script([
                command("delete", "from pathlib import Path; Path('obsolete.txt').unlink()"),
                command("check", "from pathlib import Path; assert not Path('obsolete.txt').exists()",
                        purpose="check"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Remove the obsolete file")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.changed_paths, ["obsolete.txt"])
            self.assertEqual(result.unrefreshed_paths, [])

    def test_incomplete_observation_cannot_certify_a_command(self):
        with tempfile.TemporaryDirectory() as directory:
            class UnreadableWorkspace(Workspace):
                def snapshot(self):
                    observed = super().snapshot()
                    return Snapshot(observed.files, ("blocked.txt: injected I/O failure",))

            workspace = UnreadableWorkspace(Path(directory))
            result = Agent(workspace, Script([command("check", "print('PASS')", purpose="check")]),
                           ToolGate(workspace, lambda _n, _a: True)).ask("Check")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.verified_commands, [])
            self.assertTrue(result.observation_errors)

    def test_existing_dirty_files_do_not_become_agent_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "human.txt").write_text("preexisting work")
            workspace = Workspace(root)
            result = Agent(workspace, Script([read("human.txt")]),
                           ToolGate(workspace, lambda _n, _a: True)).ask("Read existing work")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.changed_paths, [])

    def test_failed_later_check_revokes_earlier_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("1")
            workspace = Workspace(root)
            result = Agent(workspace, Script([
                command("edit", "from pathlib import Path; Path('value.txt').write_text('0')"),
                read("value.txt"),
                command("pass", "from pathlib import Path; assert Path('value.txt').read_text() == '0'",
                        purpose="check"),
                command("fail", "from pathlib import Path; assert Path('value.txt').read_text() == '1'",
                        purpose="check"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Edit and check")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.verified_commands, [])
            self.assertIsNone(result.verification_fingerprint)

    def test_external_write_while_model_answers_invalidates_the_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "value.txt"
            path.write_text("1")
            workspace = Workspace(root)

            class HumanEdit(Script):
                def complete(self, messages, tools):
                    turn = super().complete(messages, tools)
                    if not turn.calls:
                        path.write_text("0")
                    return turn

            result = Agent(workspace, HumanEdit([
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '1'",
                        purpose="check"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Check the current value")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.verified_commands, [])
            self.assertEqual(result.changed_paths, ["value.txt"])

    def test_command_change_invalidates_earlier_check_even_after_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("1")
            workspace = Workspace(root)
            model = Script([
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '1'",
                        purpose="check"),
                command("change", "from pathlib import Path; Path('value.txt').write_text('0')"),
                read("value.txt"),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Check then modify")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.changed_paths, ["value.txt"])
            self.assertEqual(result.verified_commands, [])
