import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class ProcessRecoveryTests(unittest.TestCase):
    def test_killed_worker_preserves_effect_boundary_and_archived_reads_do_not_unlock_replay(self):
        worker = Path(__file__).parent / "helpers" / "interruption_worker.py"
        for phase in ("before-effect", "after-effect", "after-result"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process = subprocess.Popen([sys.executable, str(worker), str(root), phase],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 15
                    while not (root / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue((root / "ready").exists(), "worker did not reach the requested boundary")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                session_id = (root / "session-id").read_text()
                archives = list((root / ".cobalt" / "outputs").glob("output-*"))
                turns = []
                if phase != "before-effect":
                    self.assertEqual(len(archives), 1)
                    turns.append(ModelTurn("", (ToolCall("recall", "read_output", {
                        "output_id": archives[0].name, "query": "RESULT=",
                    }),)))
                if phase != "after-result":
                    turns.append(ModelTurn("", (ToolCall("blind-retry", "run_command", {
                        "argv": [sys.executable, "-c", "from pathlib import Path; Path('replayed').write_text('bad')"],
                    }),)))
                turns.extend([
                    ModelTurn("", (ToolCall("inspect", "list_files", {}),)),
                    ModelTurn("Inspected the current workspace."),
                ])

                class ResumedModel:
                    def __init__(self, scripted_turns):
                        self.turns = iter(scripted_turns)
                        self.seen = []

                    def complete(self, messages, tools):
                        self.seen.append(list(messages))
                        return next(self.turns)

                model = ResumedModel(turns)
                workspace = Workspace(root)
                result = Agent(workspace, model, ToolGate(workspace, lambda _n, _a: True), resume=session_id).ask("Inspect without replay")
                self.assertEqual(result.status, "completed")
                self.assertFalse((root / "replayed").exists())
                self.assertEqual((root / "counter").read_text() if (root / "counter").exists() else "0",
                                 "0" if phase == "before-effect" else "1")
                observed = "\n".join(m.get("content") or "" for m in model.seen[-1])
                if phase != "before-effect":
                    self.assertIn("RESULT=once", observed)
                if phase != "after-result":
                    self.assertIn("status: denied", observed)
