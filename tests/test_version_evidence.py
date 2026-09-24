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


def command(call_id, code):
    return ModelTurn("", (ToolCall(call_id, "run_command", {"argv": [sys.executable, "-c", code]}),))


def read(path):
    return ModelTurn("", (ToolCall("read-" + path, "read_file", {"path": path}),))


class VersionEvidenceTests(unittest.TestCase):
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
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '2'"),
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
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '2'"),
                read("value.txt"),
            ]), ToolGate(workspace, lambda _n, _a: True)).ask("Change to 2 and check")
            self.assertEqual(result.status, "completed")
            self.assertEqual(len(result.verified_commands), 1)
            self.assertEqual(result.verification_fingerprint, workspace.snapshot().fingerprint)

    def test_deleted_file_needs_a_new_check_but_not_an_impossible_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "obsolete.txt").write_text("old")
            workspace = Workspace(root)
            result = Agent(workspace, Script([
                command("delete", "from pathlib import Path; Path('obsolete.txt').unlink()"),
                command("check", "from pathlib import Path; assert not Path('obsolete.txt').exists()"),
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
            result = Agent(workspace, Script([command("check", "print('PASS')")]),
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
                command("pass", "from pathlib import Path; assert Path('value.txt').read_text() == '0'"),
                command("fail", "from pathlib import Path; assert Path('value.txt').read_text() == '1'"),
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
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '1'"),
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
                command("check", "from pathlib import Path; assert Path('value.txt').read_text() == '1'"),
                command("change", "from pathlib import Path; Path('value.txt').write_text('0')"),
                read("value.txt"),
            ])
            result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True)).ask("Check then modify")
            self.assertEqual(result.status, "unverified")
            self.assertEqual(result.changed_paths, ["value.txt"])
            self.assertEqual(result.verified_commands, [])
