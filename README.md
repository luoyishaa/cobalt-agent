# Cobalt

Cobalt is a local coding agent for everyday repository work: ask about code,
trace a failure, make a small edit, and run a check. It records which actions
actually happened so a final answer can point to evidence.

## Start

Requires Python 3.11 or newer. Install from this directory:

```powershell
python -m pip install -e .
$env:DEEPSEEK_API_KEY = "your-key"
cobalt --workspace path\to\repository "Explain where the CLI starts"
```

You may place `DEEPSEEK_API_KEY=...` in a local `.env` inside the target
workspace. The `.env` file is ignored by Git. For interactive mode, omit the
question. File edits and commands ask for approval unless `--yes` is set.
The default model is `deepseek-flash` through DeepSeek's chat completions API.
Use `--mode ask` to expose only read-only tools. `--mode code` is the default.

## What is different

- The model returns native structured tool calls, which the runtime validates.
- File edits require the digest from a prior read, preventing stale overwrites.
- Commands receive an argument array and run without a shell.
- The answer is marked unverified if an edit has no later successful check.
- After an edit, the agent must reread the changed file before its final answer.
- Every run has an event log and a compact result under `.cobalt/runs/`.
- If a process stops mid-tool, session resume marks that call's effect unknown
  and requires inspection; it does not run the tool again automatically.
- Explicit `file:line` references are checked against fresh reads. A completed
  run records actions; it does not certify every sentence in the answer.

## Verify

```powershell
python -m unittest discover -s tests -v
```

Read [the architecture guide](docs/ARCHITECTURE.md) for the design decisions
and [the evaluation protocol](docs/EVALUATION.md) for measured results and limits.
