# Architecture

Cobalt runs one user-facing coding assistant. It can answer questions about a
repository, investigate failures, edit files, and run checks. A task type does
not create a separate agent. The same run loop applies a different set of tools
and evidence requirements to each request.

## Design rules

1. **Observed actions outrank fluent answers.** A repository answer without a
   successful repository tool is marked `unverified`. An edit without a later
   successful command is also marked `unverified`.
2. **One workspace root.** File tools resolve each path before access. Escapes,
   runtime directories, and local `.env` files are blocked.
3. **Edits are conditional.** `replace_text` needs the digest returned by
   `read_file`. If a human or another process has changed the file, the edit
   fails and the agent must read again.
4. **The model requests actions; the program decides whether to execute.**
   Native function calls supply a name and JSON arguments. The tool gate
   validates them, applies read-only mode, and asks for approval before writes
   or commands.
5. **Evidence survives a run.** An append-only event file records model turns
   and tool outcomes. A result file holds the outcome and measured usage.
6. **Failure stays visible.** Invalid model structure gets at most two recovery
   attempts; tool errors are sent back to the model. A timed-out command is
   terminated and cannot count as a successful check.

## Main path

```text
CLI
  -> workspace + session + model adapter
  -> Agent.ask(request)
      -> select bounded conversation context
      -> ask model for structured tool calls or answer
      -> validate and execute tools through ToolGate
      -> record outcomes in session and run journal
      -> repeat, or return an answer with verification status
```

The `Agent` module has one public operation, `ask`. It hides turn bookkeeping,
limits, tool result feedback, and completion rules. `Workspace` owns the file
and command rules. `Model` is the external seam: the first adapter speaks the
DeepSeek chat-completions protocol. A scripted adapter is used in tests.

The session file supports continuing a conversation after a new CLI process
starts. The context selector drops only complete older user turns, so no tool
reply is separated from its call. The evidence book retains a few file excerpts
with digests; changed files are labeled stale and must be read again.

## Extension points

- **Models:** add another adapter that returns `ModelTurn`; keep provider wire
  formats out of the run loop.
- **Tools:** define a JSON schema and an implementation behind `ToolGate`.
  Changes to permissions and approval remain in one place.
- **Interfaces:** a future terminal UI or web client can call `Agent.ask` and
  read the run journal. Neither needs to duplicate tool policy.
- **Evaluation:** each case runs in a fresh copy of its fixture and gets an
  external verifier. Scripted runtime tests and live model tasks are reported
  separately.

## Trust limits

File path checks and approval reduce accidental damage. They do not turn an
approved arbitrary command into a sandbox: the command can access resources
permitted to the local user. Run unknown commands in a disposable environment.
The default CLI asks before commands and edits; `--yes` is intended for
controlled evaluation workspaces.

The evidence status is deliberately narrow. A passing command proves that
command exited successfully; it does not prove every requirement is met.
External verification in a benchmark provides a second, independent check.
Conditional edits detect a changed file when the digest is checked; they do
not provide a transaction against a writer that races with the final replace.
New-file creation uses an atomic create-only link, while replacement retains
the existing file's permission bits. Session resume continues between requests;
it does not replay an interrupted command or finish a partially executed run.
