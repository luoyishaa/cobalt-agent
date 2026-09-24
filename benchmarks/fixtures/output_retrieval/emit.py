"""Emit a one-time result buried in a large output; every invocation has an effect."""

import json
import secrets
import sys
from pathlib import Path

mode = sys.argv[1]
if mode not in {"ok", "fail"}:
    raise SystemExit("expected ok or fail")
state = Path(".cobalt") / "receipt.json"
state.parent.mkdir(exist_ok=True)
executions = json.loads(state.read_text())["executions"] + 1 if state.exists() else 1
token = secrets.token_hex(24)
state.write_text(json.dumps({"executions": executions, "token": token}), encoding="utf-8")
print("progress " * 9000, flush=True)
stream = sys.stderr if mode == "fail" else sys.stdout
print("diagnostic " * 7000, file=stream)
print("RESULT=" + token, file=stream)
print("trailing " * 9000, file=stream, flush=True)
raise SystemExit(7 if mode == "fail" else 0)
