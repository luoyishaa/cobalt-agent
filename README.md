# Cobalt

Cobalt is a local coding agent for everyday repository work: ask about code,
trace a failure, make a small edit, and run a check. It records which actions
actually happened so a final answer can point to evidence.

## Start on Windows (PowerShell)

Requires Python 3.11 or newer and a key for the model provider you select.
Docker is needed only for container execution. From a fresh checkout:

```powershell
git clone https://github.com/luoyishaa/cobalt-agent.git
Set-Location cobalt-agent
python -m venv .venv
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -e .
Copy-Item .env.example .env
notepad .env
```

In `.env`, fill `DEEPSEEK_API_KEY` for the default DeepSeek provider, then save
the file. If you already have a local `.env`, keep it instead of copying the
example again. The file is ignored by Git. In a new PowerShell window, return
to this checkout and set `$py = ".\.venv\Scripts\python.exe"` again. On
macOS/Linux, use `python3 -m venv .venv`, `.venv/bin/python`, and
`cp .env.example .env` for the corresponding setup steps.

Keep the terminal in Cobalt's checkout. `--workspace` points to the
**repository you want Cobalt to inspect or edit**; it can also be this checkout.
The default `.env` is read
from the terminal's current directory, not from that target repository.

## Talk to Cobalt

Replace `D:\work\my-project` with the path to your target repository. To start
a read-only conversation:

```powershell
& $py -m cobalt --workspace "D:\work\my-project" --mode ask
```

Type natural-language questions at `cobalt>`; use `/exit` or `/quit` to leave.
Put a question at the end of the command for a single turn instead:

```powershell
& $py -m cobalt --workspace "D:\work\my-project" --mode ask "Where is the CLI parser defined?"
```

To let Cobalt edit the **original** repository, omit `--mode ask`. The default
is `--mode code --execution local`; each edit and command asks for your approval:

```powershell
& $py -m cobalt --workspace "D:\work\my-project" "Fix the failing test and run it"
```

To work in an **isolated copy**, build the command image once, then start a
conversation in container mode. `--yes` allows edits and commands in that copy
without individual prompts; it is not accepted with local execution:

```powershell
docker build -t cobalt/python:3.11 -f docker/Dockerfile .
& $py -m cobalt --doctor
& $py -m cobalt --workspace "D:\work\my-project" --execution container --yes
```

Container mode prints `Isolated workspace: ...`. Your original repository is
not edited. Review the printed run report and its `final.patch` before applying
changes yourself. `--doctor` checks local prerequisites and the image, not API
balance or remote model availability.

| Option | Meaning |
| --- | --- |
| `--mode ask` / `--mode code` | Read-only tools / edits and commands allowed; `code` is the default. |
| `--execution local` / `--execution container` | Work directly in the target repository with approvals / work in an isolated copy; `local` is the default. |
| `--yes` | Auto-approve actions, only in container mode. |
| `--task-file task.json` | Optional one-turn task with user checks, protected paths, and an optional required-change rule. Do not also pass a question. |
| `--resume latest` | Start a new turn with the saved conversation in the same workspace. Add `--continue` only for an unfinished task after a tool limit or model error. |
| `--report RUN_ID` | Read a saved report without calling the model. |
| `--max-tool-calls N` | Maximum tool calls per run; default 16. |

For container resume, use the printed **isolated workspace** as `--workspace`.
If the task used `--task-file`, pass the same file again when using `--continue`.
A free-text request can ask for a repair; `--mode code` only permits edits and
does not require them. A structured task can set `"require_change": true` when
an actual repository change is part of its acceptance contract. See
[execution and task checks](docs/EXECUTION.md) for the JSON format and recovery
details.

The default model is `deepseek-flash` via DeepSeek. To change providers, set
`COBALT_PROVIDER` and that provider's key in `.env`; set `COBALT_MODEL_TIER=pro`
or `COBALT_MODEL_ID` for a model override. CLI flags `--provider`, `--model`,
`--base-url`, and `--env-file` override the corresponding settings. Process
environment values take priority over `.env`. See
[model providers](docs/PROVIDERS.md) for presets and protocol limits.

## Run the demo

On Windows, after configuring a local `.env`, run:

```powershell
docker build -t cobalt/python:3.11 -f docker/Dockerfile .
.\scripts\demo.ps1
```

The script copies a small broken project into a temporary directory, confirms
its test fails, asks Cobalt to repair an isolated copy, then runs a verifier
outside the agent's workspace. It prints the workspace and run-record paths so the result
can be inspected. The real model is nondeterministic; a successful demo run is
one observed outcome, not a reliability estimate. API usage may incur charges.

## What is different

- The model returns native structured tool calls, which the runtime validates.
- File edits require the digest from a prior read, preventing stale overwrites.
- Commands receive an argument array and run without a shell.
- Large command outputs have durable IDs. The agent can search or page through
  the saved output without rerunning the command, including after a session restart.
  Failed-command previews retain the tail of stderr even after noisy stdout.
- The answer is marked unverified if an edit has no later successful check.
- Repair tasks can explicitly require a final repository change. A read-only
  proposed fix then remains unverified and receives bounded follow-up attempts.
- Guarded edits return a bounded readback of the persisted file version when
  available. Other changes, including command effects, require a fresh read
  before the final answer.
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
- Explicit claims that files were changed or checks ran/passed are compared
  with observed effects. Unsupported drafts remain in the run record, while
  the delivered answer names the missing evidence. This is a bounded audit of
  recognizable claims, not a semantic proof that the task was solved.
- Session memory stores file locations and digests as navigation hints, not
  truncated text presented as a summary.

## Verify

```powershell
& $py -m unittest discover -s tests -v
```

Read [the architecture guide](docs/ARCHITECTURE.md) for the design decisions
and [the evaluation protocol](docs/EVALUATION.md) for measured results and limits.
The [multi-file continuation results](docs/MULTIFILE.md) include failed recovery,
a supplementary verifier audit, and a targeted rerun after the fix.
The [fixed ten-issue SWE-bench Lite evaluation](docs/SWEBENCH-10.md) records
all attempts and official grading: 4/10 resolved across four repositories.
The [earlier single-issue evaluation](docs/SWEBENCH.md) records a failed
attempt that motivated isolated execution and clearer task validation.
