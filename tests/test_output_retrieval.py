import re
import sys
import tempfile
import unittest
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace


class OutputRetrievalTests(unittest.TestCase):
    def test_failed_and_timed_out_commands_keep_searchable_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = ToolGate(Workspace(Path(directory)), lambda _n, _a: True)
            for ending in ("sys.exit(7)", "import time; time.sleep(30)"):
                with self.subTest(ending=ending):
                    code = "import sys; print('x'*20000); print('ERR=missing-index', file=sys.stderr, flush=True); " + ending
                    outcome = gate.execute("run_command", {"argv": [sys.executable, "-c", code], "timeout": 1})
                    self.assertEqual(outcome.status, "error")
                    self.assertFalse(outcome.verified)
                    output_id = re.search(r"output_id: (output-[a-f0-9]{32})", outcome.message)[1]
                    found = gate.execute("read_output", {"output_id": output_id, "query": "ERR="})
                    self.assertEqual(found.status, "ok")
                    self.assertIn("ERR=missing-index", found.message)
                    self.assertIn("timed_out: " + ("true" if "sleep" in ending else "false"), found.message)

    def test_search_crosses_chunks_and_pages_have_stable_byte_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = ToolGate(Workspace(Path(directory)), lambda _n, _a: True)
            outcome = gate.execute("run_command", {"argv": [sys.executable, "-c",
                "import sys; sys.stdout.buffer.write(b'x'*65534 + b'NEEDLE-tail')"]})
            output_id = re.search(r"output_id: (output-[a-f0-9]{32})", outcome.message)[1]
            first = gate.execute("read_output", {"output_id": output_id, "query": "NEEDLE", "limit": 6})
            self.assertIn("byte_range: [65534, 65540)", first.message)
            self.assertTrue(first.message.endswith("NEEDLE"))
            second = gate.execute("read_output", {"output_id": output_id, "offset": 65540, "limit": 5})
            self.assertTrue(second.message.endswith("-tail"))
            for args in ({"output_id": "../secret"}, {"output_id": output_id, "offset": -1},
                         {"output_id": output_id, "limit": 9000}, {"output_id": output_id, "query": ""}):
                self.assertEqual(gate.execute("read_output", args).status, "error")
            archive = Path(directory) / ".cobalt" / "outputs" / output_id / "output.bin"
            archive.write_bytes(b"changed")
            damaged = gate.execute("read_output", {"output_id": output_id})
            self.assertEqual(damaged.status, "error")
            self.assertIn("integrity", damaged.message)

    def test_resumed_agent_can_answer_from_archived_output(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))

            class CommandModel:
                def complete(self, messages, tools):
                    if messages[-1]["role"] == "user":
                        return ModelTurn("", (ToolCall("cmd", "run_command", {
                            "argv": [sys.executable, "-c", "print('x'*20000); print('RESULT=violet'); print('y'*20000)"],
                        }),))
                    return ModelTurn("Command finished.")

            first = Agent(workspace, CommandModel(), ToolGate(workspace, lambda _n, _a: True))
            first.ask("Run once")

            class RecallModel:
                def complete(self, messages, tools):
                    if messages[-1]["role"] == "user":
                        content = next(m["content"] for m in messages if m.get("tool_call_id") == "cmd")
                        output_id = re.search(r"output_id: (output-[a-f0-9]{32})", content)[1]
                        return ModelTurn("", (ToolCall("recall", "read_output", {
                            "output_id": output_id, "query": "RESULT=", "limit": 80,
                        }),))
                    if "RESULT=violet" not in messages[-1]["content"]:
                        raise AssertionError("missing original result")
                    return ModelTurn("The historical result was violet.")

            resumed = Agent(workspace, RecallModel(), ToolGate(workspace, lambda _n, _a: False), resume=first.session_id)
            result = resumed.ask("What result did the earlier command print?")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.tool_calls, 1)

    def test_repeated_output_pages_fit_agent_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gate = ToolGate(Workspace(root), lambda _n, _a: True)
            result = gate.execute("run_command", {"argv": [sys.executable, "-c", "print('z' * 70000)"]})
            output_id = re.search(r"output_id: (output-[a-f0-9]{32})", result.message)[1]

            class PagedModel:
                def __init__(self):
                    self.turn = 0

                def complete(self, messages, tools):
                    self.turn += 1
                    if self.turn == 1:
                        return ModelTurn("", (ToolCall("inspect", "list_files", {}),) + tuple(
                            ToolCall(f"page-{index}", "read_output", {
                                "output_id": output_id, "offset": index * 8000, "limit": 8000,
                            }) for index in range(8)))
                    return ModelTurn("The archived output was inspected.")

            agent = Agent(gate.workspace, PagedModel(), gate)
            self.assertEqual(agent.ask("Inspect the archived output").status, "completed")

    def test_middle_of_large_command_output_is_retrievable_after_restart_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "emit.py").write_text(
                "from pathlib import Path\n"
                "p = Path('counter')\n"
                "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
                "print('x' * 20000)\nprint('RESULT=indigo-47')\nprint('y' * 20000)\n",
                encoding="utf-8",
            )
            gate = ToolGate(Workspace(root), lambda _n, _a: True)
            result = gate.execute("run_command", {"argv": [sys.executable, "emit.py"]})
            self.assertNotIn("RESULT=indigo-47", result.to_message())
            match = re.search(r"output_id: (output-[a-f0-9]{32})", result.message)
            self.assertIsNotNone(match, "command must provide a durable output locator")
            reader = ToolGate(Workspace(root), lambda _n, _a: False, read_only=True)
            found = reader.execute("read_output", {"output_id": match[1], "query": "RESULT="})
            self.assertEqual(found.status, "ok")
            self.assertIn("RESULT=indigo-47", found.message)
            self.assertLessEqual(len(found.to_message()), 12000)
            self.assertEqual((root / "counter").read_text(), "1")


if __name__ == "__main__":
    unittest.main()
