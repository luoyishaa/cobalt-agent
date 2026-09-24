"""A real worker which the parent kills at a signaled persistence boundary."""

import sys
import time
from pathlib import Path

from cobalt.domain import ModelTurn, ToolCall
from cobalt.engine import Agent
from cobalt.tools import ToolGate
from cobalt.workspace import Workspace

root = Path(sys.argv[1])
phase = sys.argv[2]


def ready():
    (root / "ready").write_text(phase, encoding="utf-8")
    while True:
        time.sleep(0.1)


class WorkerGate(ToolGate):
    def execute(self, name, args):
        if phase == "before-effect":
            ready()
        result = super().execute(name, args)
        if phase == "after-effect":
            ready()
        return result


class WorkerModel:
    def complete(self, messages, tools):
        if messages[-1]["role"] == "user":
            return ModelTurn("", (ToolCall("once", "run_command", {"argv": [sys.executable, "-c",
                "from pathlib import Path; p=Path('counter'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('RESULT=once')"]}),))
        ready()


workspace = Workspace(root)
agent = Agent(workspace, WorkerModel(), WorkerGate(workspace, lambda _n, _a: True))
(root / "session-id").write_text(agent.session_id, encoding="utf-8")
agent.ask("Run the counter command once")
