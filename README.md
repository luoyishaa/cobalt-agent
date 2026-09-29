# Cobalt

Cobalt is a local coding agent for everyday repository work: ask about code,
trace a failure, make a small edit, and run a check. It records which actions
actually happened so a final answer can point to evidence.

## Start

Requires Python 3.11 or newer. Install from this directory:

```powershell
python -m pip install -e .
Copy-Item .env.example .env
# Edit .env and fill DEEPSEEK_API_KEY
cobalt --workspace path\to\repository "Explain where the CLI starts"
```

The `.env` file is loaded from the directory where you launch Cobalt, not from
the repository you ask it to work on. Process environment values take priority;
`--env-file` selects another local file. The file is ignored by Git. To change
providers, set `COBALT_PROVIDER` and that provider's key in `.env`. Set
`COBALT_MODEL_TIER=pro` or `COBALT_MODEL_ID` for an exact model. CLI flags
`--provider`, `--model`, and `--base-url` override those settings. Available
presets and important protocol limits are in [provider documentation](docs/PROVIDERS.md).
For interactive mode, omit the question. File edits and commands ask for approval
unless `--yes` is set.
The default is `deepseek-flash` through DeepSeek's chat completions API.
Use `--mode ask` to expose only read-only tools. `--mode code` is the default.
Commands run on the local host. `--yes` skips approval prompts, so use it only
in a disposable environment when the repository or task is untrusted.

## Run the demo

On Windows, after configuring a local `.env`, run:

```powershell
.\scripts\demo.ps1
```

The script copies a small broken project into a temporary directory, confirms
its test fails, asks Cobalt to repair it, then runs a verifier outside the
agent's workspace. It prints the workspace and run-record paths so the result
can be inspected. The real model is nondeterministic; a successful demo run is
one observed outcome, not a reliability estimate. API usage may incur charges.

## What is different

- The model returns native structured tool calls, which the runtime validates.
- File edits require the digest from a prior read, preventing stale overwrites.
- Commands receive an argument array and run without a shell.
- Large command outputs have durable IDs. The agent can search or page through
  the saved output without rerunning the command, including after a session restart.
- The answer is marked unverified if an edit has no later successful check.
- After an edit, the agent must reread the changed file before its final answer.
- Missing final evidence receives up to two checklist reminders within the
  original tool budget. Unverified drafts stay in the run record; the delivered
  answer names the missing evidence instead of repeating unsupported confidence.
- Observed file changes invalidate earlier command evidence, including changes
  made by commands or external writers. Incomplete observations remain unverified.
- Every run has an event log and a compact result under `.cobalt/runs/`.
- If a process stops mid-tool, session resume marks that call's effect unknown
  and requires inspection. Exact interrupted command replay remains blocked
  after inspection; see [recovery behavior and limits](docs/RECOVERY.md).
- Explicit `file:line` references are checked against fresh reads. A completed
  run records actions; it does not certify every sentence in the answer.
- Session memory stores file locations and digests as navigation hints, not
  truncated text presented as a summary.

## Verify

```powershell
python -m unittest discover -s tests -v
```

Read [the architecture guide](docs/ARCHITECTURE.md) for the design decisions
and [the evaluation protocol](docs/EVALUATION.md) for measured results and limits.
The [multi-file continuation results](docs/MULTIFILE.md) include failed recovery,
a supplementary verifier audit, and a targeted rerun after the fix.
The [public issue evaluation](docs/SWEBENCH.md) records a fixed SWE-bench Lite
attempt and its grading outcome.
